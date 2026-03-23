"""Token and cost budget manager for the nanobot-stack AI assistant.

Tracks per-day token usage and estimated spend across all LLM calls,
enforces configurable daily limits, and exposes budget-pressure signals
that downstream routing can use to trigger model downgrades.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger("rag-bridge.token-budget")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TOKEN_BUDGET_ENABLED = os.getenv("TOKEN_BUDGET_ENABLED", "false").lower() == "true"
DAILY_TOKEN_BUDGET = int(os.getenv("DAILY_TOKEN_BUDGET", "5000000"))  # 5M tokens/day
DAILY_COST_BUDGET_CENTS = int(os.getenv("DAILY_COST_BUDGET_CENTS", "300"))  # $3/day
STATE_DIR = pathlib.Path(
    os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"),
)

# Per-1M-token costs in cents
MODEL_COSTS: dict[str, dict[str, int]] = {
    "gpt-4.1-mini": {"input": 4, "output": 16},
    "gpt-4.1": {"input": 20, "output": 80},
    "claude-sonnet-4-20250514": {"input": 30, "output": 150},
    "claude-haiku-4-5-20251001": {"input": 8, "output": 32},
    "text-embedding-3-large": {"input": 1, "output": 0},
}

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Ensure required tables exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS token_budgets (
            period TEXT PRIMARY KEY,
            budget_tokens INTEGER NOT NULL,
            used_tokens INTEGER DEFAULT 0,
            budget_cost_cents INTEGER DEFAULT 0,
            used_cost_cents INTEGER DEFAULT 0,
            reset_at TEXT NOT NULL
        )""",
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS token_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            operation_type TEXT NOT NULL DEFAULT 'chat',
            task_type TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            estimated_cost_cents REAL NOT NULL,
            timestamp TEXT NOT NULL
        )""",
    )


def _conn() -> sqlite3.Connection:
    """Return a connection with tables initialised."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(STATE_DIR / "token_budgets.db"))
    _init_db(db)
    return db


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_period(db: sqlite3.Connection, period: str) -> None:
    """Insert a budget row for *period* if one does not already exist."""
    db.execute(
        """INSERT OR IGNORE INTO token_budgets
           (period, budget_tokens, budget_cost_cents, used_tokens, used_cost_cents, reset_at)
           VALUES (?, ?, ?, 0, 0, ?)""",
        (period, DAILY_TOKEN_BUDGET, DAILY_COST_BUDGET_CENTS, period + "T00:00:00+00:00"),
    )


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in cents for a model call. Returns 0 for local models."""
    if model.startswith("ollama/"):
        return 0.0
    costs = MODEL_COSTS.get(model)
    if costs is None:
        logger.warning("Unknown model %r — assuming zero cost", model)
        return 0.0
    return (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Recording & querying
# ---------------------------------------------------------------------------

def record_usage(
    session_id: str,
    operation_type: str,
    task_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    """Record token usage and estimated cost.  Returns a usage summary."""
    cost = estimate_cost(model, input_tokens, output_tokens)
    total_tokens = input_tokens + output_tokens
    now_iso = datetime.now(timezone.utc).isoformat()
    period = _today()

    with _lock:
        db = _conn()
        try:
            _ensure_period(db, period)
            db.execute(
                """INSERT INTO token_usage_log
                   (session_id, operation_type, task_type, model,
                    input_tokens, output_tokens, estimated_cost_cents, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, operation_type, task_type, model,
                 input_tokens, output_tokens, cost, now_iso),
            )
            db.execute(
                """UPDATE token_budgets
                   SET used_tokens = used_tokens + ?,
                       used_cost_cents = used_cost_cents + ?
                   WHERE period = ?""",
                (total_tokens, cost, period),
            )
            db.commit()
        finally:
            db.close()

    return {
        "recorded": True,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_cents": cost,
    }


def check_budget(session_id: str = "") -> dict[str, Any]:  # pylint: disable=unused-argument
    """Check remaining budget.

    Returns a dict with ``ok``, token/cost totals, ``usage_percent``, and
    ``budget_pressure`` (0.0–1.0).
    """
    period = _today()

    with _lock:
        db = _conn()
        try:
            _ensure_period(db, period)
            db.commit()
            row = db.execute(
                """SELECT budget_tokens, used_tokens, budget_cost_cents, used_cost_cents
                   FROM token_budgets WHERE period = ?""",
                (period,),
            ).fetchone()
        finally:
            db.close()

    if row is None:
        # Shouldn't happen after _ensure_period, but be defensive.
        return {
            "ok": True,
            "daily_tokens_used": 0,
            "daily_tokens_budget": DAILY_TOKEN_BUDGET,
            "daily_cost_used_cents": 0,
            "daily_cost_budget_cents": DAILY_COST_BUDGET_CENTS,
            "usage_percent": 0.0,
            "budget_pressure": 0.0,
        }

    budget_tokens, used_tokens, budget_cost, used_cost = row
    token_pct = used_tokens / budget_tokens if budget_tokens else 0.0
    cost_pct = used_cost / budget_cost if budget_cost else 0.0
    pressure = min(max(token_pct, cost_pct), 1.0)
    usage_pct = round(pressure * 100, 2)

    return {
        "ok": pressure < 1.0,
        "daily_tokens_used": used_tokens,
        "daily_tokens_budget": budget_tokens,
        "daily_cost_used_cents": used_cost,
        "daily_cost_budget_cents": budget_cost,
        "usage_percent": usage_pct,
        "budget_pressure": round(pressure, 4),
    }


def get_budget_pressure() -> float:
    """Quick check returning just the budget pressure value (0.0–1.0)."""
    return check_budget().get("budget_pressure", 0.0)


def should_downgrade() -> bool:
    """Return ``True`` if budget pressure exceeds 80 %, suggesting a model downgrade."""
    return get_budget_pressure() > 0.8


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def get_daily_report() -> dict[str, Any]:
    """Daily cost breakdown by model and task type."""
    period = _today()

    with _lock:
        db = _conn()
        try:
            by_model = db.execute(
                """SELECT model,
                          SUM(input_tokens)  AS total_input,
                          SUM(output_tokens) AS total_output,
                          SUM(estimated_cost_cents) AS total_cost
                   FROM token_usage_log
                   WHERE timestamp LIKE ? || '%'
                   GROUP BY model""",
                (period,),
            ).fetchall()

            by_task = db.execute(
                """SELECT task_type,
                          SUM(input_tokens)  AS total_input,
                          SUM(output_tokens) AS total_output,
                          SUM(estimated_cost_cents) AS total_cost
                   FROM token_usage_log
                   WHERE timestamp LIKE ? || '%'
                   GROUP BY task_type""",
                (period,),
            ).fetchall()
        finally:
            db.close()

    return {
        "period": period,
        "by_model": [
            {"model": r[0], "input_tokens": r[1], "output_tokens": r[2],
             "cost_cents": r[3]}
            for r in by_model
        ],
        "by_task": [
            {"task_type": r[0], "input_tokens": r[1], "output_tokens": r[2],
             "cost_cents": r[3]}
            for r in by_task
        ],
    }


def get_usage_history(days: int = 7) -> list[dict[str, Any]]:
    """Return daily totals for the last *days* days."""
    with _lock:
        db = _conn()
        try:
            rows = db.execute(
                """SELECT period, budget_tokens, used_tokens,
                          budget_cost_cents, used_cost_cents
                   FROM token_budgets
                   ORDER BY period DESC
                   LIMIT ?""",
                (days,),
            ).fetchall()
        finally:
            db.close()

    return [
        {
            "period": r[0],
            "budget_tokens": r[1],
            "used_tokens": r[2],
            "budget_cost_cents": r[3],
            "used_cost_cents": r[4],
        }
        for r in rows
    ]


def reset_daily_budget() -> None:
    """Reset the daily budget counter.  Called at midnight or manually."""
    period = _today()

    with _lock:
        db = _conn()
        try:
            db.execute(
                """INSERT INTO token_budgets
                   (period, budget_tokens, budget_cost_cents, used_tokens, used_cost_cents, reset_at)
                   VALUES (?, ?, ?, 0, 0, ?)
                   ON CONFLICT(period) DO UPDATE SET
                       used_tokens = 0,
                       used_cost_cents = 0,
                       reset_at = ?""",
                (period, DAILY_TOKEN_BUDGET, DAILY_COST_BUDGET_CENTS,
                 datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
        finally:
            db.close()

    logger.info("Daily budget reset for period %s", period)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/budget", tags=["token-budget"])
_verify_token = None


def init_budget(verify_token_dep=None):
    global _verify_token
    _verify_token = verify_token_dep


@router.get("/status")
def budget_status_endpoint(request: Request):
    if _verify_token:
        _verify_token(request)
    return check_budget()


@router.get("/daily-report")
def daily_report_endpoint(request: Request):
    if _verify_token:
        _verify_token(request)
    return get_daily_report()


@router.get("/history")
def usage_history_endpoint(request: Request, days: int = 7):
    if _verify_token:
        _verify_token(request)
    return get_usage_history(days)


@router.post("/reset")
def reset_budget_endpoint(request: Request):
    if _verify_token:
        _verify_token(request)
    reset_daily_budget()
    return {"ok": True, "period": _today()}
