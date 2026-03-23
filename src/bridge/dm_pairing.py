"""DM pairing — server-side user approval gate for channel adapters.

Inspired by OpenClaw's pairing system.  When CHANNEL_DM_POLICY="pairing"
(the default), unknown users who message the bot receive a short pairing
code.  The server owner must approve the code before the user can interact:

    POST /channels/pair/{code}/approve

Once approved, the user's platform identity (e.g. "telegram:123456") is
persisted in a local SQLite allowlist and all future messages are processed
normally.

Setting CHANNEL_DM_POLICY="open" disables pairing entirely (all inbound
messages are accepted).
"""
from __future__ import annotations

import logging
import os
import pathlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.channels.pairing")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
DM_POLICY = os.getenv("CHANNEL_DM_POLICY", "pairing").lower()
PAIRING_CODE_EXPIRY = int(os.getenv("PAIRING_CODE_EXPIRY", "30"))  # minutes
PAIRING_DB_PATH = STATE_DIR / "channel_pairing.db"

# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _init_db() -> sqlite3.Connection:
    PAIRING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(PAIRING_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS allowed_users (
        platform_id TEXT PRIMARY KEY,
        approved_at TEXT NOT NULL,
        approved_by TEXT DEFAULT 'admin',
        display_name TEXT DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pending_pairings (
        code TEXT PRIMARY KEY,
        platform_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        display_name TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    )""")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def is_user_approved(platform_id: str) -> bool:
    """Check if a platform user is in the allowlist."""
    if DM_POLICY == "open":
        return True
    with _lock:
        db = _init_db()
        try:
            row = db.execute("SELECT 1 FROM allowed_users WHERE platform_id = ?",
                             (platform_id,)).fetchone()
            return row is not None
        finally:
            db.close()


def create_pairing_code(platform_id: str, platform_name: str,
                         display_name: str = "") -> str:
    """Generate a pairing code for an unknown user.

    If a pending code already exists for this user and is not expired,
    return the same code.
    """
    now = datetime.now(timezone.utc)

    with _lock:
        db = _init_db()
        try:
            # Check for existing non-expired pending code
            row = db.execute(
                "SELECT code FROM pending_pairings "
                "WHERE platform_id = ? AND status = 'pending' AND expires_at > ?",
                (platform_id, now.isoformat()),
            ).fetchone()
            if row:
                return row[0]

            # Generate new code (6 chars, alphanumeric, easy to type)
            code = secrets.token_hex(3).upper()  # e.g. "A1B2C3"
            expires = now + timedelta(minutes=PAIRING_CODE_EXPIRY)
            db.execute(
                "INSERT INTO pending_pairings "
                "(code, platform_id, platform_name, display_name, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (code, platform_id, platform_name, display_name,
                 now.isoformat(), expires.isoformat()),
            )
            db.commit()
            return code
        finally:
            db.close()


def list_pending() -> list[dict[str, Any]]:
    """Return all non-expired pending pairing requests."""
    _expire_stale()
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT code, platform_id, platform_name, display_name, created_at, expires_at "
                "FROM pending_pairings WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
            keys = ["code", "platform_id", "platform_name", "display_name",
                    "created_at", "expires_at"]
            return [dict(zip(keys, r)) for r in rows]
        finally:
            db.close()


def approve_pairing(code: str, approved_by: str = "admin") -> dict[str, Any]:
    """Approve a pairing code, adding the user to the allowlist."""
    with _lock:
        db = _init_db()
        try:
            row = db.execute(
                "SELECT platform_id, platform_name, display_name "
                "FROM pending_pairings WHERE code = ? AND status = 'pending'",
                (code,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"Pairing code '{code}' not found or already used"}

            platform_id, platform_name, display_name = row
            now = datetime.now(timezone.utc).isoformat()

            db.execute(
                "INSERT OR REPLACE INTO allowed_users "
                "(platform_id, approved_at, approved_by, display_name) "
                "VALUES (?, ?, ?, ?)",
                (platform_id, now, approved_by, display_name),
            )
            db.execute("UPDATE pending_pairings SET status = 'approved' WHERE code = ?",
                       (code,))
            db.commit()

            _audit("approved", platform_id, approved_by)
            return {"ok": True, "platform_id": platform_id,
                    "platform_name": platform_name, "display_name": display_name}
        finally:
            db.close()


def reject_pairing(code: str) -> dict[str, Any]:
    """Reject a pairing code."""
    with _lock:
        db = _init_db()
        try:
            row = db.execute(
                "SELECT platform_id FROM pending_pairings "
                "WHERE code = ? AND status = 'pending'", (code,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"Pairing code '{code}' not found or already processed"}

            db.execute("UPDATE pending_pairings SET status = 'rejected' WHERE code = ?",
                       (code,))
            db.commit()
            _audit("rejected", row[0])
            return {"ok": True, "code": code, "status": "rejected"}
        finally:
            db.close()


def revoke_user(platform_id: str) -> dict[str, Any]:
    """Remove a user from the allowlist."""
    with _lock:
        db = _init_db()
        try:
            cur = db.execute("DELETE FROM allowed_users WHERE platform_id = ?",
                             (platform_id,))
            db.commit()
            if cur.rowcount == 0:
                return {"ok": False, "error": f"User '{platform_id}' not in allowlist"}
            _audit("revoked", platform_id)
            return {"ok": True, "platform_id": platform_id, "status": "revoked"}
        finally:
            db.close()


def list_approved_users() -> list[dict[str, Any]]:
    """Return all approved users."""
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT platform_id, approved_at, approved_by, display_name "
                "FROM allowed_users ORDER BY approved_at DESC"
            ).fetchall()
            keys = ["platform_id", "approved_at", "approved_by", "display_name"]
            return [dict(zip(keys, r)) for r in rows]
        finally:
            db.close()


def _expire_stale() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        db = _init_db()
        try:
            cur = db.execute(
                "UPDATE pending_pairings SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at < ?", (now,),
            )
            db.commit()
            return cur.rowcount
        finally:
            db.close()


def _audit(action: str, platform_id: str, user: str = "") -> None:
    try:
        from audit import log_audit_event
        log_audit_event("channel_pairing", {
            "action": action, "platform_id": platform_id, "user": user,
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/channels/pair", tags=["channel-pairing"])
_verify_token = None


def init_pairing(verify_token_dep=None):
    global _verify_token
    _verify_token = verify_token_dep


@router.get("/pending")
def pending_endpoint(request: Request):
    """List all pending pairing requests awaiting approval."""
    if _verify_token:
        _verify_token(request)
    return {"pending": list_pending(), "policy": DM_POLICY}


@router.get("/users")
def users_endpoint(request: Request):
    """List all approved channel users."""
    if _verify_token:
        _verify_token(request)
    return {"users": list_approved_users(), "policy": DM_POLICY}


@router.post("/{code}/approve")
def approve_endpoint(code: str, request: Request):
    """Approve a pairing code, granting the user access."""
    if _verify_token:
        _verify_token(request)
    result = approve_pairing(code)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/{code}/reject")
def reject_endpoint(code: str, request: Request):
    """Reject a pairing code."""
    if _verify_token:
        _verify_token(request)
    result = reject_pairing(code)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class RevokeIn(BaseModel):
    platform_id: str


@router.post("/revoke")
def revoke_endpoint(body: RevokeIn, request: Request):
    """Revoke a user's access (remove from allowlist)."""
    if _verify_token:
        _verify_token(request)
    result = revoke_user(body.platform_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
