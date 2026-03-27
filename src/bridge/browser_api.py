"""Browser API — FastAPI router for browser automation endpoints.

Exposes:
  POST /api/browser/run          — run a browser automation task
  GET  /api/browser/sessions     — list recent browser sessions
  GET  /api/browser/action-log   — list recent browser action log entries
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.browser-api")

router = APIRouter(prefix="/api/browser", tags=["browser"])

_browser_agent: Any = None
_verify_token_dep: Any = None


def init_browser_api(browser_agent: Any = None, verify_token_dep: Any = None) -> None:
    """Wire the browser agent and auth dependency into this router."""
    global _browser_agent, _verify_token_dep  # pylint: disable=global-statement
    _browser_agent = browser_agent
    _verify_token_dep = verify_token_dep


def _get_browser_agent() -> Any:
    """Return the configured browser agent instance."""
    return _browser_agent


def _get_browser_db_path() -> str:
    """Return the path to browser.db based on current env."""
    state_dir = os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")
    return str(pathlib.Path(state_dir) / "browser.db")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class BrowserRunRequest(BaseModel):
    """Request body for POST /api/browser/run."""
    task: str
    context: dict | None = None


class BrowserRunResponse(BaseModel):
    """Response body for POST /api/browser/run."""
    status: str
    output: str
    actions_taken: list[dict] = []
    cost_tokens: int = 0
    artifacts: dict = {}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_action_log(limit: int = 100, session_id: str | None = None) -> list[dict]:
    """Fetch entries from browser_action_log."""
    db_path = _get_browser_db_path()
    if not pathlib.Path(db_path).exists():
        return []
    try:
        db = sqlite3.connect(db_path)
        try:
            if session_id:
                rows = db.execute(
                    "SELECT id, session_id, action_type, url, selector, status, trust_level, "
                    "approved_by, started_at, duration_ms, error_msg "
                    "FROM browser_action_log WHERE session_id = ? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id, session_id, action_type, url, selector, status, trust_level, "
                    "approved_by, started_at, duration_ms, error_msg "
                    "FROM browser_action_log ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            keys = ["id", "session_id", "action_type", "url", "selector", "status",
                    "trust_level", "approved_by", "started_at", "duration_ms", "error_msg"]
            return [dict(zip(keys, row)) for row in rows]
        finally:
            db.close()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to read browser_action_log: %s", exc)
        return []


def _get_sessions(limit: int = 50) -> list[dict]:
    """Fetch distinct sessions from browser_action_log."""
    db_path = _get_browser_db_path()
    if not pathlib.Path(db_path).exists():
        return []
    try:
        db = sqlite3.connect(db_path)
        try:
            rows = db.execute(
                "SELECT session_id, MIN(started_at) AS started_at, COUNT(*) AS action_count "
                "FROM browser_action_log GROUP BY session_id ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            keys = ["session_id", "started_at", "action_count"]
            return [dict(zip(keys, row)) for row in rows]
        finally:
            db.close()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to read sessions from browser_action_log: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=BrowserRunResponse)
async def browser_run(body: BrowserRunRequest) -> BrowserRunResponse:
    """Run a browser automation task."""
    # Check at request time (not module load time) to support monkeypatching in tests
    if os.getenv("BROWSER_ENABLED", "false").lower() != "true":
        return BrowserRunResponse(
            status="disabled",
            output="Browser automation is disabled. Set BROWSER_ENABLED=true to enable.",
        )

    agent = _get_browser_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="Browser agent not initialised")

    result = await agent.run(body.task, context=body.context)
    return BrowserRunResponse(
        status=result.status,
        output=result.output,
        actions_taken=result.actions_taken,
        cost_tokens=result.cost_tokens,
        artifacts=result.artifacts,
    )


@router.get("/sessions")
def browser_sessions(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """List recent browser sessions."""
    sessions = _get_sessions(limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/action-log")
def browser_action_log(
    limit: int = Query(default=100, ge=1, le=1000),
    session_id: str | None = Query(default=None),
) -> dict:
    """List recent browser action log entries."""
    entries = _get_action_log(limit=limit, session_id=session_id)
    return {"entries": entries, "count": len(entries)}
