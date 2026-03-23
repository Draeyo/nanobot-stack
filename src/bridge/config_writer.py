"""Config writer — approval-gated configuration changes.

The agent proposes config modifications which are validated, staged, and
diffed.  A user reviews and applies (or rejects/rolls back).
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.config-writer")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
RAG_HOME = pathlib.Path(os.getenv("RAG_HOME", "/opt/nanobot-stack/rag-bridge"))
CONFIG_WRITER_ENABLED = os.getenv("CONFIG_WRITER_ENABLED", "false").lower() == "true"
CONFIG_CHANGE_EXPIRY = int(os.getenv("CONFIG_CHANGE_EXPIRY", "60"))
STAGING_DIR = STATE_DIR / "config_staging"
BACKUP_DIR = STATE_DIR / "config_backups"
CONFIG_DB_PATH = STATE_DIR / "config_changes.db"

# ---------------------------------------------------------------------------
# Allowlisted config files (filename -> format type)
# ---------------------------------------------------------------------------
ALLOWED_CONFIG_FILES: dict[str, str] = {
    ".env": "env",
    "model_router.json": "json",
    "NANOBOT_POLICY_PROMPT.md": "markdown",
}


def _resolve_path(file_name: str) -> pathlib.Path | None:
    """Map a config filename to its actual path."""
    paths = {
        ".env": RAG_HOME / ".env",
        "model_router.json": pathlib.Path(
            os.getenv("MODEL_ROUTER_FILE", str(RAG_HOME / "model_router.json"))
        ),
        "NANOBOT_POLICY_PROMPT.md": pathlib.Path(
            os.getenv("NANOBOT_POLICY_FILE",
                       "/opt/nanobot-stack/nanobot/config/NANOBOT_POLICY_PROMPT.md")
        ),
    }
    return paths.get(file_name)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_json_content(content: str) -> list[str]:
    errors = []
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            errors.append("Root element must be a JSON object")
        elif "model_router.json" and ("profiles" not in data or "task_routes" not in data):
            errors.append("model_router.json must have 'profiles' and 'task_routes' keys")
    except json.JSONDecodeError as e:
        errors.append(f"JSON syntax error: {e}")
    return errors


def validate_env_content(content: str) -> list[str]:
    errors = []
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            errors.append(f"Line {i}: missing '=' separator")
    return errors


def validate_markdown_content(content: str) -> list[str]:
    if not content.strip():
        return ["Content is empty"]
    return []


_VALIDATORS = {
    "json": validate_json_content,
    "env": validate_env_content,
    "markdown": validate_markdown_content,
}


def _validate(file_name: str, content: str) -> list[str]:
    file_type = ALLOWED_CONFIG_FILES.get(file_name, "unknown")
    validator = _VALIDATORS.get(file_type)
    if validator:
        return validator(content)
    return [f"Unknown file type: {file_type}"]


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _init_db() -> sqlite3.Connection:
    CONFIG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CONFIG_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS config_changes (
        id TEXT PRIMARY KEY,
        file_name TEXT NOT NULL,
        change_type TEXT NOT NULL DEFAULT 'modify',
        proposed_content TEXT NOT NULL,
        diff_preview TEXT DEFAULT '',
        proposed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        validation_errors TEXT DEFAULT '[]',
        proposed_by TEXT DEFAULT 'agent',
        applied_by TEXT DEFAULT '',
        backup_path TEXT DEFAULT '',
        description TEXT DEFAULT ''
    )""")
    conn.commit()
    return conn


def _row_to_dict(row: tuple) -> dict[str, Any]:
    keys = ["id", "file_name", "change_type", "proposed_content", "diff_preview",
            "proposed_at", "expires_at", "status", "validation_errors",
            "proposed_by", "applied_by", "backup_path", "description"]
    d = dict(zip(keys, row))
    d["validation_errors"] = json.loads(d.get("validation_errors", "[]"))
    return d


def _audit(change_id: str, transition: str, file_name: str, user: str = "") -> None:
    try:
        from audit import log_audit_event
        log_audit_event("config_writer", {
            "change_id": change_id, "transition": transition,
            "file_name": file_name, "user": user,
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def propose_config_change(file_name: str, content: str,
                          description: str = "", proposed_by: str = "agent") -> dict[str, Any]:
    if file_name not in ALLOWED_CONFIG_FILES:
        return {"ok": False, "error": f"File '{file_name}' is not in the allowlist. "
                f"Allowed: {', '.join(ALLOWED_CONFIG_FILES.keys())}"}

    errors = _validate(file_name, content)

    # Compute diff against current file
    diff_preview = ""
    actual_path = _resolve_path(file_name)
    if actual_path and actual_path.exists():
        current = actual_path.read_text(encoding="utf-8").splitlines(keepends=True)
        proposed = content.splitlines(keepends=True)
        diff_lines = difflib.unified_diff(current, proposed,
                                           fromfile=f"current/{file_name}",
                                           tofile=f"proposed/{file_name}")
        diff_preview = "".join(diff_lines)

    # Stage the content
    change_id = uuid.uuid4().hex[:12]
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staging_file = (STAGING_DIR / f"{change_id}_{file_name}").resolve()
    try:
        STAGING_DIR.resolve()
    except FileNotFoundError:
        # If STAGING_DIR was removed between mkdir and here, fail safely
        return {"ok": False, "error": "Staging directory is unavailable."}
    if staging_file.parent != STAGING_DIR.resolve():
        return {"ok": False, "error": "Invalid file name for staging."}
    staging_file.write_text(content, encoding="utf-8")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=CONFIG_CHANGE_EXPIRY)
    status = "validated" if not errors else "pending"

    with _lock:
        db = _init_db()
        try:
            db.execute(
                "INSERT INTO config_changes "
                "(id, file_name, proposed_content, diff_preview, proposed_at, expires_at, "
                "status, validation_errors, proposed_by, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (change_id, file_name, content, diff_preview, now.isoformat(),
                 expires.isoformat(), status, json.dumps(errors), proposed_by, description),
            )
            db.commit()
        finally:
            db.close()

    _audit(change_id, "proposed", file_name, proposed_by)
    return {"ok": True, "change_id": change_id, "file_name": file_name,
            "status": status, "validation_errors": errors,
            "diff_preview": diff_preview[:3000], "expires_at": expires.isoformat()}


def list_pending_changes() -> list[dict[str, Any]]:
    _expire_stale()
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT * FROM config_changes WHERE status IN ('pending', 'validated') "
                "ORDER BY proposed_at DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            db.close()


def get_change(change_id: str) -> dict[str, Any] | None:
    with _lock:
        db = _init_db()
        try:
            row = db.execute("SELECT * FROM config_changes WHERE id = ?", (change_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            db.close()


def preview_diff(change_id: str) -> str:
    change = get_change(change_id)
    if not change:
        return ""
    return change.get("diff_preview", "")


def apply_change(change_id: str, applied_by: str = "user") -> dict[str, Any]:
    change = get_change(change_id)
    if not change:
        return {"ok": False, "error": "change not found"}
    if change["status"] not in ("pending", "validated"):
        return {"ok": False, "error": f"change status is '{change['status']}'"}
    if change["validation_errors"]:
        return {"ok": False, "error": "change has validation errors", "errors": change["validation_errors"]}

    actual_path = _resolve_path(change["file_name"])
    if not actual_path:
        return {"ok": False, "error": f"cannot resolve path for '{change['file_name']}'"}

    # Backup current file
    backup_path_str = ""
    if actual_path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_file = BACKUP_DIR / f"{ts}_{change['file_name']}"
        shutil.copy2(str(actual_path), str(backup_file))
        backup_path_str = str(backup_file)

    # Write new content
    actual_path.parent.mkdir(parents=True, exist_ok=True)
    actual_path.write_text(change["proposed_content"], encoding="utf-8")

    with _lock:
        db = _init_db()
        try:
            db.execute(
                "UPDATE config_changes SET status = 'applied', applied_by = ?, backup_path = ? "
                "WHERE id = ?",
                (applied_by, backup_path_str, change_id),
            )
            db.commit()
        finally:
            db.close()

    _audit(change_id, "applied", change["file_name"], applied_by)
    return {"ok": True, "change_id": change_id, "status": "applied",
            "backup_path": backup_path_str}


def reject_change(change_id: str, reason: str = "") -> dict[str, Any]:
    change = get_change(change_id)
    if not change:
        return {"ok": False, "error": "change not found"}

    with _lock:
        db = _init_db()
        try:
            db.execute("UPDATE config_changes SET status = 'rejected', description = ? WHERE id = ?",
                       (reason or change.get("description", ""), change_id))
            db.commit()
        finally:
            db.close()

    _audit(change_id, "rejected", change["file_name"])
    return {"ok": True, "change_id": change_id, "status": "rejected"}


def rollback_change(change_id: str) -> dict[str, Any]:
    change = get_change(change_id)
    if not change:
        return {"ok": False, "error": "change not found"}
    if change["status"] != "applied":
        return {"ok": False, "error": f"change status is '{change['status']}', expected 'applied'"}

    backup_path = change.get("backup_path", "")
    if not backup_path or not pathlib.Path(backup_path).exists():
        return {"ok": False, "error": "backup file not found, cannot rollback"}

    actual_path = _resolve_path(change["file_name"])
    if not actual_path:
        return {"ok": False, "error": f"cannot resolve path for '{change['file_name']}'"}

    shutil.copy2(backup_path, str(actual_path))

    with _lock:
        db = _init_db()
        try:
            db.execute("UPDATE config_changes SET status = 'rolled_back' WHERE id = ?", (change_id,))
            db.commit()
        finally:
            db.close()

    _audit(change_id, "rolled_back", change["file_name"])
    return {"ok": True, "change_id": change_id, "status": "rolled_back"}


def _expire_stale() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        db = _init_db()
        try:
            cur = db.execute(
                "UPDATE config_changes SET status = 'expired' "
                "WHERE status IN ('pending', 'validated') AND expires_at < ?",
                (now,),
            )
            db.commit()
            return cur.rowcount
        finally:
            db.close()


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/config", tags=["config-writer"])
_verify_token_fn = None


def init_config_writer(verify_token_dep=None):
    global _verify_token_fn
    _verify_token_fn = verify_token_dep


class ProposeConfigIn(BaseModel):
    file_name: str
    content: str
    description: str = ""


@router.post("/propose")
def propose_endpoint(body: ProposeConfigIn, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    return propose_config_change(body.file_name, body.content, body.description)


@router.get("/pending")
def pending_endpoint(request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    return list_pending_changes()


@router.get("/history")
def config_history_endpoint(request: Request, limit: int = 50):
    """Return all config changes (not just pending), most recent first."""
    if _verify_token_fn:
        _verify_token_fn(request)
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT * FROM config_changes ORDER BY proposed_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            db.close()


@router.get("/{change_id}")
def get_change_endpoint(change_id: str, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    change = get_change(change_id)
    if not change:
        raise HTTPException(status_code=404, detail="change not found")
    return change


@router.get("/{change_id}/preview")
def preview_endpoint(change_id: str, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    diff = preview_diff(change_id)
    if not diff:
        return {"diff": "(no diff — new file or change not found)"}
    return {"diff": diff}


@router.post("/{change_id}/apply")
def apply_endpoint(change_id: str, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    return apply_change(change_id)


@router.post("/{change_id}/reject")
def reject_endpoint(change_id: str, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    return reject_change(change_id)


@router.post("/{change_id}/rollback")
def rollback_endpoint(change_id: str, request: Request):
    if _verify_token_fn:
        _verify_token_fn(request)
    return rollback_change(change_id)
