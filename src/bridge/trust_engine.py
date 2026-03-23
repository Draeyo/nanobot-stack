"""Trust engine — tiered execution gating for agent actions.

Actions are assigned one of four trust levels:
  - auto: execute immediately, optional notification
  - notify_then_execute: send notification, execute after delay (cancel window)
  - approval_required: queue for explicit user approval
  - blocked: refuse immediately

Trust levels can be promoted manually or automatically after a configurable
number of consecutive successful executions.  Every decision is audit-logged.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.trust-engine")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

TRUST_ENGINE_ENABLED = os.getenv("TRUST_ENGINE_ENABLED", "true").lower() == "true"
TRUST_DEFAULT_LEVEL = os.getenv("TRUST_DEFAULT_LEVEL", "approval_required")
TRUST_AUTO_PROMOTE_THRESHOLD = int(os.getenv("TRUST_AUTO_PROMOTE_THRESHOLD", "20"))
TRUST_NOTIFY_CHANNEL = os.getenv("TRUST_NOTIFY_CHANNEL", "")
TRUST_ROLLBACK_WINDOW_HOURS = int(os.getenv("TRUST_ROLLBACK_WINDOW_HOURS", "24"))

TRUST_DB_PATH = STATE_DIR / "trust.db"

TRUST_LEVELS = ["blocked", "approval_required", "notify_then_execute", "auto"]

# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _init_db() -> sqlite3.Connection:
    TRUST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TRUST_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS trust_policies (
        action_type TEXT PRIMARY KEY,
        trust_level TEXT NOT NULL DEFAULT 'approval_required',
        auto_promote_after INTEGER DEFAULT 0,
        successful_executions INTEGER DEFAULT 0,
        failed_executions INTEGER DEFAULT 0,
        last_promoted_at TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS trust_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL,
        action_detail TEXT NOT NULL,
        trust_level TEXT NOT NULL,
        outcome TEXT NOT NULL,
        rollback_info TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def _policy_row_to_dict(row: tuple) -> dict[str, Any]:
    keys = [
        "action_type", "trust_level", "auto_promote_after",
        "successful_executions", "failed_executions",
        "last_promoted_at", "updated_at",
    ]
    return dict(zip(keys, row))


def _audit_row_to_dict(row: tuple) -> dict[str, Any]:
    keys = [
        "id", "action_type", "action_detail", "trust_level",
        "outcome", "rollback_info", "created_at",
    ]
    return dict(zip(keys, row))


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_trust_level(action_type: str) -> str:
    """Get current trust level for an action type. Returns default if not configured."""
    with _lock:
        db = _init_db()
        try:
            row = db.execute(
                "SELECT trust_level FROM trust_policies WHERE action_type = ?",
                (action_type,),
            ).fetchone()
            return row[0] if row else TRUST_DEFAULT_LEVEL
        finally:
            db.close()


def _ensure_policy(db: sqlite3.Connection, action_type: str) -> None:
    """Create a default policy row if one does not already exist."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR IGNORE INTO trust_policies "
        "(action_type, trust_level, auto_promote_after, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (action_type, TRUST_DEFAULT_LEVEL, TRUST_AUTO_PROMOTE_THRESHOLD, now),
    )
    db.commit()


def check_and_execute(
    action_type: str,
    action_detail: str,
    action_fn: Callable[[], Any],
    rollback_fn: Callable[[], Any] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Core gate: checks trust level and acts accordingly.

    - auto: calls *action_fn* immediately
    - notify_then_execute: records pending_notify audit entry, returns audit_id
    - approval_required: returns approval_required status
    - blocked: returns error
    """
    if not TRUST_ENGINE_ENABLED:
        # Engine disabled — pass-through, execute directly.
        try:
            result = action_fn()
            return {"ok": True, "status": "executed", "result": result}
        except Exception:
            logger.exception("Action failed (trust engine disabled): %s", action_type)
            return {"ok": False, "error": "action execution failed"}

    level = get_trust_level(action_type)

    if level == "blocked":
        _record_audit(action_type, action_detail, level, "blocked")
        return {"ok": False, "error": "action blocked by trust policy"}

    if level == "approval_required":
        _record_audit(action_type, action_detail, level, "awaiting_approval")
        return {
            "ok": True,
            "status": "approval_required",
            "action_type": action_type,
            "action_detail": action_detail,
            "description": description,
        }

    if level == "notify_then_execute":
        # Record as pending — actual delayed execution handled by background scheduler.
        audit_id = _record_audit(action_type, action_detail, level, "pending_notify")
        if TRUST_NOTIFY_CHANNEL:
            logger.info(
                "Trust notification [%s]: %s — %s (cancel_id=%d)",
                TRUST_NOTIFY_CHANNEL, action_type, action_detail, audit_id,
            )
        return {
            "ok": True,
            "status": "pending_notify",
            "cancel_id": audit_id,
            "action_type": action_type,
            "description": description,
        }

    # level == "auto"
    try:
        result = action_fn()
        rollback_info = ""
        if rollback_fn is not None:
            # Store rollback reference — the callable itself is not persisted,
            # but callers can pass a serialisable description in action_detail.
            rollback_info = f"rollback available for {action_type}"
        record_outcome(action_type, action_detail, "success", rollback_info)
        return {"ok": True, "status": "executed", "result": result}
    except Exception:
        logger.exception("Auto-execute failed: %s", action_type)
        record_outcome(action_type, action_detail, "failure")
        return {"ok": False, "error": "action execution failed"}


def _record_audit(
    action_type: str,
    action_detail: str,
    trust_level: str,
    outcome: str,
    rollback_info: str = "",
) -> int:
    """Insert an audit row and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        db = _init_db()
        try:
            cur = db.execute(
                "INSERT INTO trust_audit "
                "(action_type, action_detail, trust_level, outcome, rollback_info, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action_type, action_detail, trust_level, outcome, rollback_info, now),
            )
            db.commit()
            return cur.lastrowid  # type: ignore[return-value]
        finally:
            db.close()


def record_outcome(
    action_type: str,
    action_detail: str,
    outcome: str,
    rollback_info: str = "",
) -> None:
    """Record success/failure and check auto-promotion threshold."""
    now = datetime.now(timezone.utc).isoformat()

    # Record in audit trail
    _record_audit(action_type, action_detail, get_trust_level(action_type), outcome, rollback_info)

    with _lock:
        db = _init_db()
        try:
            _ensure_policy(db, action_type)

            if outcome == "success":
                db.execute(
                    "UPDATE trust_policies SET successful_executions = successful_executions + 1, "
                    "updated_at = ? WHERE action_type = ?",
                    (now, action_type),
                )
            else:
                # Any non-success resets the consecutive success counter.
                db.execute(
                    "UPDATE trust_policies SET failed_executions = failed_executions + 1, "
                    "successful_executions = 0, updated_at = ? WHERE action_type = ?",
                    (now, action_type),
                )
            db.commit()

            # Check auto-promotion threshold.
            row = db.execute(
                "SELECT trust_level, auto_promote_after, successful_executions "
                "FROM trust_policies WHERE action_type = ?",
                (action_type,),
            ).fetchone()
        finally:
            db.close()

    if row and outcome == "success":
        level, threshold, successes = row
        if 0 < threshold <= successes:
            _auto_promote(action_type, level)


def _auto_promote(action_type: str, current_level: str) -> None:
    """Promote one step up if not already at maximum."""
    idx = TRUST_LEVELS.index(current_level) if current_level in TRUST_LEVELS else -1
    if idx < 0 or idx >= len(TRUST_LEVELS) - 1:
        return  # already at 'auto' or unknown

    new_level = TRUST_LEVELS[idx + 1]
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _init_db()
        try:
            db.execute(
                "UPDATE trust_policies SET trust_level = ?, successful_executions = 0, "
                "last_promoted_at = ?, updated_at = ? WHERE action_type = ?",
                (new_level, now, now, action_type),
            )
            db.commit()
        finally:
            db.close()

    logger.info("Auto-promoted '%s': %s -> %s", action_type, current_level, new_level)


def set_trust_level(action_type: str, level: str) -> dict[str, Any]:
    """Admin override to set trust level."""
    if level not in TRUST_LEVELS:
        return {"ok": False, "error": f"invalid trust level: {level}"}

    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _init_db()
        try:
            _ensure_policy(db, action_type)
            db.execute(
                "UPDATE trust_policies SET trust_level = ?, updated_at = ? WHERE action_type = ?",
                (level, now, action_type),
            )
            db.commit()
        finally:
            db.close()

    logger.info("Trust level set: %s -> %s", action_type, level)
    return {"ok": True, "action_type": action_type, "trust_level": level}


def promote(action_type: str) -> dict[str, Any]:
    """Promote trust level one step up."""
    current = get_trust_level(action_type)

    if current not in TRUST_LEVELS:
        return {"ok": False, "error": f"unknown current level: {current}"}

    idx = TRUST_LEVELS.index(current)
    if idx >= len(TRUST_LEVELS) - 1:
        return {"ok": False, "error": f"already at maximum trust level: {current}"}

    new_level = TRUST_LEVELS[idx + 1]
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _init_db()
        try:
            _ensure_policy(db, action_type)
            db.execute(
                "UPDATE trust_policies SET trust_level = ?, successful_executions = 0, "
                "last_promoted_at = ?, updated_at = ? WHERE action_type = ?",
                (new_level, now, now, action_type),
            )
            db.commit()
        finally:
            db.close()

    logger.info("Manually promoted '%s': %s -> %s", action_type, current, new_level)
    return {
        "ok": True,
        "action_type": action_type,
        "previous_level": current,
        "trust_level": new_level,
    }


def cancel_pending(audit_id: int) -> dict[str, Any]:
    """Cancel a notify_then_execute action before it executes."""
    with _lock:
        db = _init_db()
        try:
            row = db.execute(
                "SELECT id, outcome FROM trust_audit WHERE id = ?", (audit_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "audit entry not found"}

            if row[1] != "pending_notify":
                return {
                    "ok": False,
                    "error": f"cannot cancel: current outcome is '{row[1]}', expected 'pending_notify'",
                }

            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE trust_audit SET outcome = 'cancelled', rollback_info = ? WHERE id = ?",
                (f"cancelled at {now}", audit_id),
            )
            db.commit()
        finally:
            db.close()

    logger.info("Cancelled pending action audit_id=%d", audit_id)
    return {"ok": True, "audit_id": audit_id, "status": "cancelled"}


def get_policies() -> list[dict[str, Any]]:
    """List all trust policies."""
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT * FROM trust_policies ORDER BY action_type"
            ).fetchall()
            return [_policy_row_to_dict(r) for r in rows]
        finally:
            db.close()


def get_audit_log(
    limit: int = 100,
    action_type: str | None = None,
) -> list[dict[str, Any]]:
    """Get trust audit entries."""
    with _lock:
        db = _init_db()
        try:
            if action_type:
                rows = db.execute(
                    "SELECT * FROM trust_audit WHERE action_type = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (action_type, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM trust_audit ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_audit_row_to_dict(r) for r in rows]
        finally:
            db.close()


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/trust", tags=["trust-engine"])
_verify_token = None


def init_trust(verify_token_dep=None):
    global _verify_token
    _verify_token = verify_token_dep


class SetTrustLevelIn(BaseModel):
    trust_level: str


@router.get("/policies")
def list_policies_endpoint(request: Request):
    """List all trust policies."""
    if _verify_token:
        _verify_token(request)
    return {"policies": get_policies()}


@router.post("/policies/{action_type}")
def set_policy_endpoint(action_type: str, body: SetTrustLevelIn, request: Request):
    """Update trust level for an action type."""
    if _verify_token:
        _verify_token(request)
    result = set_trust_level(action_type, body.trust_level)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/audit")
def audit_endpoint(
    request: Request,
    action_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get trust audit log with optional filters."""
    if _verify_token:
        _verify_token(request)
    return {"entries": get_audit_log(limit=limit, action_type=action_type)}


@router.post("/promote/{action_type}")
def promote_endpoint(action_type: str, request: Request):
    """Manually promote an action type one trust level up."""
    if _verify_token:
        _verify_token(request)
    result = promote(action_type)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/cancel/{audit_id}")
def cancel_endpoint(audit_id: int, request: Request):
    """Cancel a pending notify_then_execute action."""
    if _verify_token:
        _verify_token(request)
    result = cancel_pending(audit_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
