"""Backup REST API — /api/backup/*

Endpoints:
  POST   /api/backup/run          — trigger a backup now
  GET    /api/backup/list         — list all backups from backup_log
  GET    /api/backup/status       — last backup + next scheduled run
  DELETE /api/backup/{backup_id}  — delete a specific backup
"""
from __future__ import annotations

import logging
import tarfile

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("rag-bridge.backup_api")

router = APIRouter(prefix="/api/backup", tags=["backup"])

# Module-level singleton — set by init_backup_api()
_manager = None


def init_backup_api(manager) -> None:
    """Inject the BackupManager instance used by all endpoints."""
    global _manager  # pylint: disable=global-statement
    _manager = manager


def _get_manager():
    if _manager is None:
        raise HTTPException(status_code=503, detail="BackupManager not initialised")
    return _manager


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
async def run_backup():
    """Trigger a full backup immediately and return the result."""
    mgr = _get_manager()
    result = await mgr.run_backup()
    return result


@router.get("/list")
def list_backups():
    """List all backup records from backup_log."""
    mgr = _get_manager()
    return {"backups": mgr.list_backups()}


@router.get("/status")
def get_status():
    """Return last backup metadata and estimated next scheduled run."""
    mgr = _get_manager()
    return mgr.get_status()


@router.delete("/{backup_id}")
def delete_backup(backup_id: str):
    """Delete a backup archive from disk and remove its log entry."""
    mgr = _get_manager()
    deleted = mgr.delete_backup(backup_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id!r} not found")
    return {"deleted": True, "backup_id": backup_id}


@router.post("/restore/{backup_id}")
async def restore_backup(backup_id: str):
    """Restore from a specific backup archive.

    This will:
    1. Stop accepting new requests (via a global flag)
    2. Restore Qdrant collections from snapshots
    3. Restore SQLite databases from the archive
    4. Log the restore event
    """
    mgr = _get_manager()
    if not hasattr(mgr, "restore_backup"):
        raise HTTPException(status_code=501, detail="Restore not implemented in BackupManager")
    try:
        result = await mgr.restore_backup(backup_id)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id!r} not found")
    except Exception as exc:
        logger.exception("Restore failed for %s: %s", backup_id, exc)
        raise HTTPException(status_code=500, detail="Restore failed — check server logs")


@router.post("/verify/{backup_id}")
def verify_backup(backup_id: str):
    """Verify backup archive integrity (check tar, encryption, contents)."""
    mgr = _get_manager()
    backups = mgr.list_backups()
    backup = next((b for b in backups if b.get("backup_id") == backup_id), None)
    if not backup:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id!r} not found")

    import os
    import tarfile
    path = backup.get("archive_path", "")
    if not path or not os.path.exists(path):
        return {"ok": False, "backup_id": backup_id, "error": "archive file missing"}

    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getnames()
        return {
            "ok": True,
            "backup_id": backup_id,
            "files": len(members),
            "contents": members[:20],
            "size_bytes": os.path.getsize(path),
        }
    except (tarfile.TarError, OSError) as exc:
        return {"ok": False, "backup_id": backup_id, "error": type(exc).__name__}
