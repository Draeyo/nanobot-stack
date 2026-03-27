"""encryption_api.py — REST endpoints for Sub-projet L encryption lifecycle management.

Endpoints:
  GET  /api/encryption/status           — current encryption state
  POST /api/encryption/enable           — start encrypt-all migration
  POST /api/encryption/disable          — start decrypt-all migration
  POST /api/encryption/rotate           — re-encrypt with new master key
  GET  /api/encryption/migration-status — progress of current/last migration job
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.encryption_api")

router = APIRouter(prefix="/api/encryption", tags=["encryption"])

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_encryptor_sqlite = None   # FieldEncryptor for sqlite-v1
_encryptor_qdrant = None   # FieldEncryptor for qdrant-v1
_qdrant_client = None
_state_dir: str = os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")

_migration_job: dict[str, Any] = {}
_migration_lock = threading.Lock()


def init_encryption_api(
    encryptor_sqlite=None,
    encryptor_qdrant=None,
    qdrant_client=None,
    state_dir: str | None = None,
) -> None:
    """Inject dependencies. Called by app.py at startup."""
    global _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir  # pylint: disable=global-statement
    _encryptor_sqlite = encryptor_sqlite
    _encryptor_qdrant = encryptor_qdrant
    _qdrant_client = qdrant_client
    if state_dir:
        _state_dir = state_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_count_encrypted(db_path: str, table: str, column: str) -> tuple[int, int]:
    """Return (encrypted_count, total_count) for a SQLite column."""
    try:
        db = sqlite3.connect(db_path)
        try:
            total = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            enc = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE 'enc:v1:%'"
            ).fetchone()[0]
            return enc, total
        finally:
            db.close()
    except Exception:  # pylint: disable=broad-except
        return 0, 0


def _qdrant_count_encrypted(collection: str, field: str) -> tuple[int, int]:
    """Return (encrypted_count, total_count) for a Qdrant collection field."""
    if _qdrant_client is None:
        return 0, 0
    try:
        total = _qdrant_client.count(collection_name=collection).count
        enc_count = 0
        offset = None
        while True:
            results, offset = _qdrant_client.scroll(
                collection_name=collection,
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            for point in results:
                val = (point.payload or {}).get(field, "")
                if isinstance(val, str) and val.startswith("enc:v1:"):
                    enc_count += 1
            if offset is None:
                break
        return enc_count, total
    except Exception:  # pylint: disable=broad-except
        return 0, 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status() -> dict:
    """Return current encryption state and per-field encrypted counts."""
    enc_enabled = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"
    sqlite_enabled = os.getenv(
        "ENCRYPTION_SQLITE_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
    ).lower() == "true"
    qdrant_enabled = os.getenv(
        "ENCRYPTION_QDRANT_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
    ).lower() == "true"

    state_dir = _state_dir
    kg_db = os.path.join(state_dir, "knowledge_graph.db")
    scheduler_db = os.path.join(state_dir, "scheduler.db")

    ke_enc, ke_total = _sqlite_count_encrypted(kg_db, "entities", "description")
    sl_enc, sl_total = _sqlite_count_encrypted(scheduler_db, "email_sync_log", "account")

    mp_enc, mp_total = _qdrant_count_encrypted("memory_personal", "text")
    ei_enc, ei_total = _qdrant_count_encrypted("email_inbox", "subject")
    ce_enc, ce_total = _qdrant_count_encrypted("calendar_events", "description")

    def _partial(enc: int, total: int) -> bool:
        return total > 0 and 0 < enc < total

    partially_encrypted = any([
        _partial(ke_enc, ke_total),
        _partial(sl_enc, sl_total),
        _partial(mp_enc, mp_total),
        _partial(ei_enc, ei_total),
        _partial(ce_enc, ce_total),
    ])

    return {
        "encryption_enabled": enc_enabled,
        "sqlite_enabled": sqlite_enabled,
        "qdrant_enabled": qdrant_enabled,
        "partially_encrypted": partially_encrypted,
        "sqlite_fields_encrypted": {
            "entities.description": ke_enc,
            "email_sync_log.account": sl_enc,
        },
        "qdrant_fields_encrypted": {
            "memory_personal.text": mp_enc,
            "email_inbox.subject": ei_enc,
            "calendar_events.description": ce_enc,
        },
        "migration_in_progress": bool(_migration_job.get("status") == "in_progress"),
    }


class RotateRequest(BaseModel):
    new_master_key: str


@router.post("/enable")
async def enable_encryption() -> dict:
    """Start async migration: encrypt all plaintext values in-place (idempotent)."""
    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        if _encryptor_sqlite is None and _encryptor_qdrant is None:
            raise HTTPException(
                status_code=400,
                detail="ENCRYPTION_MASTER_KEY is absent — cannot run migration"
            )
        job_id = f"enc-mig-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "enable", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_enable_migration(job_id))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Migration de chiffrement démarrée.",
    }


@router.post("/disable")
async def disable_encryption() -> dict:
    """Start async migration: decrypt all enc:v1: values back to plaintext."""
    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        if _encryptor_sqlite is None and _encryptor_qdrant is None:
            raise HTTPException(
                status_code=400,
                detail="ENCRYPTION_MASTER_KEY is absent — cannot decrypt without the key"
            )
        job_id = f"enc-dis-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "disable", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_disable_migration(job_id))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Migration de déchiffrement démarrée.",
    }


@router.post("/rotate")
async def rotate_key(body: RotateRequest) -> dict:
    """Re-encrypt all data with a new master key."""
    new_key = body.new_master_key.strip()
    if len(new_key) != 64:
        raise HTTPException(status_code=400, detail="new_master_key must be exactly 64 hex characters")
    try:
        bytes.fromhex(new_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="new_master_key contains invalid hex characters") from exc

    current_key = os.getenv("ENCRYPTION_MASTER_KEY", "")
    if new_key == current_key:
        raise HTTPException(status_code=400, detail="new_master_key is identical to the current key")

    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        job_id = f"enc-rot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "rotate", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_rotation_migration(job_id, new_key))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Rotation de clé démarrée.",
    }


@router.get("/migration-status")
def get_migration_status() -> dict:
    """Return progress of the current or last migration job."""
    if not _migration_job:
        return {"job_id": None, "status": "no_job", "progress": {}}
    return dict(_migration_job)


# ---------------------------------------------------------------------------
# Background migration helpers
# ---------------------------------------------------------------------------

async def _run_enable_migration(job_id: str) -> None:
    """Async task: encrypt all plaintext values in SQLite and Qdrant."""
    from encryption_migrations import run_enable_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_enable_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir
            ),
        )
        with _migration_lock:
            _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Enable migration failed: %s", exc)
        with _migration_lock:
            _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})
    logger.debug("Enable migration task finished: %s", job_id)


async def _run_disable_migration(job_id: str) -> None:
    """Async task: decrypt all enc:v1: values in SQLite and Qdrant."""
    from encryption_migrations import run_disable_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_disable_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir
            ),
        )
        with _migration_lock:
            _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Disable migration failed: %s", exc)
        with _migration_lock:
            _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})
    logger.debug("Disable migration task finished: %s", job_id)


async def _run_rotation_migration(job_id: str, new_key_hex: str) -> None:
    """Async task: re-encrypt all values with new_key_hex."""
    from encryption_migrations import run_rotation_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_rotation_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir, new_key_hex
            ),
        )
        with _migration_lock:
            _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Rotation migration failed: %s", exc)
        with _migration_lock:
            _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})
    logger.debug("Rotation migration task finished: %s", job_id)
