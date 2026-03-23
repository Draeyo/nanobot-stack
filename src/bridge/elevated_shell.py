"""Elevated shell — approval-gated mutating system commands.

The agent proposes commands (restart services, install packages, modify files)
which are stored as pending actions. A user must explicitly approve before
execution.  Every transition is audit-logged.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.elevated-shell")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
ELEVATED_ENABLED = os.getenv("ELEVATED_SHELL_ENABLED", "false").lower() == "true"
ELEVATED_TIMEOUT = int(os.getenv("ELEVATED_SHELL_TIMEOUT", "60"))
ACTION_EXPIRY_MINUTES = int(os.getenv("ELEVATED_ACTION_EXPIRY", "30"))
ELEVATED_DB_PATH = STATE_DIR / "elevated_actions.db"

# ---------------------------------------------------------------------------
# Elevated command allow-list (mutating commands requiring approval)
# ---------------------------------------------------------------------------
# Default built-in commands.  Users can extend or restrict via env vars:
#   ELEVATED_EXTRA_COMMANDS  — JSON object to add/override commands.
#       Example: '{"npm": ["install","uninstall"], "snap": true}'
#   ELEVATED_DISABLED_COMMANDS — comma-separated binaries to remove from defaults.
#       Example: "rm,chmod,chown"
_DEFAULT_ELEVATED_COMMANDS: dict[str, bool | list[str]] = {
    "systemctl": ["restart", "stop", "start", "enable", "disable", "reload"],
    "apt": ["install", "update", "upgrade", "remove", "autoremove"],
    "pip": ["install", "uninstall"],
    "docker": ["run", "exec", "stop", "restart", "rm", "compose"],
    "cp": True,
    "mv": True,
    "rm": True,
    "mkdir": True,
    "tee": True,
    "chmod": True,
    "chown": True,
    "ln": True,
    "cat": True,  # elevated cat allows any file, unlike read-only cat
}


def _build_elevated_commands() -> dict[str, bool | list[str]]:
    """Build the final elevated command set from defaults + user overrides."""
    commands = dict(_DEFAULT_ELEVATED_COMMANDS)

    # Remove disabled commands
    disabled_raw = os.getenv("ELEVATED_DISABLED_COMMANDS", "")
    if disabled_raw:
        for binary in disabled_raw.split(","):
            binary = binary.strip()
            if binary:
                commands.pop(binary, None)

    # Merge extra commands (overrides take precedence)
    extra_raw = os.getenv("ELEVATED_EXTRA_COMMANDS", "")
    if extra_raw:
        try:
            extra = json.loads(extra_raw)
            if isinstance(extra, dict):
                for binary, spec in extra.items():
                    if isinstance(spec, bool) or isinstance(spec, list):
                        commands[binary] = spec
                    else:
                        logger.warning("ELEVATED_EXTRA_COMMANDS: ignoring invalid spec for '%s'", binary)
            else:
                logger.warning("ELEVATED_EXTRA_COMMANDS must be a JSON object, got %s", type(extra).__name__)
        except json.JSONDecodeError as e:
            logger.error("ELEVATED_EXTRA_COMMANDS: invalid JSON: %s", e)

    return commands


ELEVATED_COMMANDS: dict[str, bool | list[str]] = _build_elevated_commands()


def validate_elevated_command(cmd: str) -> tuple[bool, str]:
    """Check if a command is in the elevated allow-list."""
    parts = cmd.strip().split()
    if not parts:
        return False, "empty command"

    binary = parts[0].split("/")[-1]

    if binary not in ELEVATED_COMMANDS:
        return False, f"binary '{binary}' not in elevated allow-list"

    allowed = ELEVATED_COMMANDS[binary]
    if allowed is True:
        return True, "allowed"

    if isinstance(allowed, list):
        if len(parts) > 1 and parts[1] in allowed:
            return True, "allowed"
        if len(parts) == 1:
            return True, "allowed (no subcommand)"
        return False, f"subcommand '{parts[1]}' not allowed for '{binary}'"

    return False, "unknown allow-list format"


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _init_db() -> sqlite3.Connection:
    ELEVATED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ELEVATED_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY,
        command TEXT NOT NULL,
        description TEXT DEFAULT '',
        proposed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        proposed_by TEXT DEFAULT 'agent',
        approved_by TEXT DEFAULT '',
        executed_at TEXT DEFAULT '',
        result_json TEXT DEFAULT '{}',
        audit_note TEXT DEFAULT ''
    )""")
    conn.commit()
    return conn


def _row_to_dict(row: tuple) -> dict[str, Any]:
    keys = ["id", "command", "description", "proposed_at", "expires_at",
            "status", "proposed_by", "approved_by", "executed_at", "result_json", "audit_note"]
    d = dict(zip(keys, row))
    d["result"] = json.loads(d.pop("result_json", "{}"))
    return d


def _audit(action_id: str, transition: str, command: str, user: str = "") -> None:
    try:
        from audit import log_audit_event
        log_audit_event("elevated_shell", {
            "action_id": action_id, "transition": transition,
            "command": command, "user": user,
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def propose_action(command: str, description: str = "", proposed_by: str = "agent") -> dict[str, Any]:
    """Validate and store a pending elevated action."""
    ok, reason = validate_elevated_command(command)
    if not ok:
        return {"ok": False, "error": f"Command not allowed: {reason}"}

    action_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ACTION_EXPIRY_MINUTES)

    with _lock:
        db = _init_db()
        try:
            db.execute(
                "INSERT INTO actions (id, command, description, proposed_at, expires_at, proposed_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action_id, command, description, now.isoformat(), expires.isoformat(), proposed_by),
            )
            db.commit()
        finally:
            db.close()

    _audit(action_id, "proposed", command, proposed_by)
    return {"ok": True, "action_id": action_id, "command": command,
            "status": "pending", "expires_at": expires.isoformat()}


def list_pending() -> list[dict[str, Any]]:
    """Return all non-expired pending actions."""
    expire_stale()
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT * FROM actions WHERE status = 'pending' ORDER BY proposed_at DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            db.close()


def get_action(action_id: str) -> dict[str, Any] | None:
    with _lock:
        db = _init_db()
        try:
            row = db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            db.close()


def approve_action(action_id: str, approved_by: str = "user", auto_execute: bool = True) -> dict[str, Any]:
    """Approve a pending action. Optionally auto-execute it."""
    action = get_action(action_id)
    if not action:
        return {"ok": False, "error": "action not found"}
    if action["status"] != "pending":
        return {"ok": False, "error": f"action status is '{action['status']}', expected 'pending'"}

    with _lock:
        db = _init_db()
        try:
            db.execute("UPDATE actions SET status = 'approved', approved_by = ? WHERE id = ?",
                       (approved_by, action_id))
            db.commit()
        finally:
            db.close()

    _audit(action_id, "approved", action["command"], approved_by)

    if auto_execute:
        return execute_approved(action_id)
    return {"ok": True, "action_id": action_id, "status": "approved"}


def reject_action(action_id: str, reason: str = "") -> dict[str, Any]:
    action = get_action(action_id)
    if not action:
        return {"ok": False, "error": "action not found"}
    if action["status"] != "pending":
        return {"ok": False, "error": f"action status is '{action['status']}', expected 'pending'"}

    with _lock:
        db = _init_db()
        try:
            db.execute("UPDATE actions SET status = 'rejected', audit_note = ? WHERE id = ?",
                       (reason, action_id))
            db.commit()
        finally:
            db.close()

    _audit(action_id, "rejected", action["command"])
    return {"ok": True, "action_id": action_id, "status": "rejected"}


def execute_approved(action_id: str) -> dict[str, Any]:
    """Execute an approved action."""
    action = get_action(action_id)
    if not action:
        return {"ok": False, "error": "action not found"}
    if action["status"] not in ("approved",):
        return {"ok": False, "error": f"action status is '{action['status']}', expected 'approved'"}

    command = action["command"]
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=ELEVATED_TIMEOUT,
            env={**os.environ, "LANG": "C.UTF-8"},
            check=False,
        )
        exec_result = {
            "ok": True, "stdout": result.stdout[:8000],
            "stderr": result.stderr[:4000], "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        exec_result = {"ok": False, "error": f"timeout after {ELEVATED_TIMEOUT}s"}
    except Exception:
        logger.exception("Unexpected error while executing elevated action %s: %r", action_id, command)
        exec_result = {
            "ok": False,
            "error": "unexpected error while executing command",
        }

    with _lock:
        db = _init_db()
        try:
            db.execute(
                "UPDATE actions SET status = 'executed', executed_at = ?, result_json = ? WHERE id = ?",
                (now, json.dumps(exec_result, ensure_ascii=False), action_id),
            )
            db.commit()
        finally:
            db.close()

    _audit(action_id, "executed", command)
    return {"ok": True, "action_id": action_id, "status": "executed", "result": exec_result}


def expire_stale() -> int:
    """Mark expired pending actions. Returns count expired."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        db = _init_db()
        try:
            cur = db.execute(
                "UPDATE actions SET status = 'expired' WHERE status = 'pending' AND expires_at < ?",
                (now,),
            )
            db.commit()
            return cur.rowcount
        finally:
            db.close()


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/actions", tags=["elevated-shell"])
_verify_token = None


def init_elevated(verify_token_dep=None):
    global _verify_token
    _verify_token = verify_token_dep


class ProposeIn(BaseModel):
    command: str
    description: str = ""


@router.post("/propose")
def propose_endpoint(body: ProposeIn, request: Request):
    if _verify_token:
        _verify_token(request)
    return propose_action(body.command, body.description)


@router.get("/pending")
def pending_endpoint(request: Request):
    if _verify_token:
        _verify_token(request)
    return list_pending()


@router.get("/history")
def history_endpoint(request: Request, limit: int = 50):
    """Return all actions (not just pending), most recent first."""
    if _verify_token:
        _verify_token(request)
    with _lock:
        db = _init_db()
        try:
            rows = db.execute(
                "SELECT * FROM actions ORDER BY proposed_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            db.close()


@router.get("/{action_id}")
def get_action_endpoint(action_id: str, request: Request):
    if _verify_token:
        _verify_token(request)
    action = get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="action not found")
    return action


@router.post("/{action_id}/approve")
def approve_endpoint(action_id: str, request: Request):
    if _verify_token:
        _verify_token(request)
    return approve_action(action_id)


@router.post("/{action_id}/reject")
def reject_endpoint(action_id: str, request: Request):
    if _verify_token:
        _verify_token(request)
    return reject_action(action_id)


@router.post("/{action_id}/execute")
def execute_endpoint(action_id: str, request: Request):
    if _verify_token:
        _verify_token(request)
    return execute_approved(action_id)


@router.get("/commands/list")
def list_commands_endpoint(request: Request):
    """Return the current elevated command allow-list (defaults + user overrides)."""
    if _verify_token:
        _verify_token(request)
    result = {}
    for binary, spec in ELEVATED_COMMANDS.items():
        result[binary] = {"subcommands": spec if isinstance(spec, list) else "*"}
    return {"commands": result, "total": len(result)}
