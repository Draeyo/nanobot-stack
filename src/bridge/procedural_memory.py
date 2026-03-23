"""Procedural memory — learn repeatable workflows from observed action patterns.

Watches the stream of tool executions across sessions, detects recurring
action sequences via LLM analysis, and offers to replay learned workflows
when a matching trigger is recognised.

- log_action: record every tool execution (fast INSERT, no LLM)
- detect_patterns: periodically scan the action log for recurring workflows
- match_workflow / suggest_workflow: find learned workflows for a query
- execute_workflow: replay a learned workflow step-by-step
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.procedural-memory")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

PROCEDURAL_MEMORY_ENABLED = os.getenv("PROCEDURAL_MEMORY_ENABLED", "false").lower() == "true"
DETECT_THRESHOLD = int(os.getenv("PROCEDURAL_DETECT_THRESHOLD", "10"))
SCAN_WINDOW = int(os.getenv("PROCEDURAL_SCAN_WINDOW", "100"))
SUGGEST_CONFIDENCE = float(os.getenv("PROCEDURAL_SUGGEST_CONFIDENCE", "0.7"))

_lock = threading.Lock()
_last_detect_id: int = 0

DETECT_PROMPT = """Analyze these action sequences from user sessions and identify recurring workflow patterns.

Actions (most recent first):
{actions}

For each pattern found, return:
- trigger_pattern: a short description of what triggers this workflow (e.g., "check server health", "deploy update")
- steps: ordered list of actions that form the workflow

Return ONLY JSON:
{{
  "patterns": [
    {{
      "trigger_pattern": "description",
      "steps": [
        {{"action": "action_type", "params": {{"key": "value_template"}}, "description": "what this step does"}}
      ],
      "frequency": how_many_times_observed
    }}
  ]
}}

Only report patterns observed 2+ times. Be specific about action parameters."""


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    """Return a connection to the procedural memory database, creating tables if needed."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(STATE_DIR / "procedural_memory.db"))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS action_sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_pattern TEXT NOT NULL UNIQUE,
        trigger_embedding_id TEXT DEFAULT '',
        steps_json TEXT NOT NULL,
        frequency INTEGER DEFAULT 1,
        last_observed TEXT NOT NULL,
        last_executed TEXT DEFAULT '',
        success_rate REAL DEFAULT 1.0,
        confidence REAL DEFAULT 0.0,
        auto_suggest BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        action TEXT NOT NULL,
        params_json TEXT NOT NULL,
        result_summary TEXT DEFAULT '',
        timestamp TEXT NOT NULL
    )""")
    return db


# ---------------------------------------------------------------------------
# Action logging
# ---------------------------------------------------------------------------

def log_action(session_id: str, action: str, params: dict, result_summary: str = "") -> None:
    """Log an action to the action_log table. Called after every tool execution."""
    if not PROCEDURAL_MEMORY_ENABLED:
        return
    now = datetime.now(timezone.utc).isoformat()
    summary = (result_summary or "")[:500]
    with _lock:
        db = _conn()
        try:
            db.execute(
                "INSERT INTO action_log (session_id, action, params_json, result_summary, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, action, json.dumps(params), summary, now),
            )
            db.commit()
        finally:
            db.close()


def get_action_count_since_last_detect() -> int:
    """Return the number of new actions since the last detect_patterns run."""
    global _last_detect_id  # noqa: PLW0602
    try:
        db = _conn()
        row = db.execute(
            "SELECT COUNT(*) FROM action_log WHERE id > ?", (_last_detect_id,)
        ).fetchone()
        db.close()
        return row[0] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def detect_patterns(run_chat_fn: Callable[..., dict]) -> dict:
    """Analyze the action log for recurring sequences using LLM.

    Only runs if DETECT_THRESHOLD or more new actions have been logged since
    the last detection.  Scans the last SCAN_WINDOW actions at most.

    Returns ``{"detected": int, "new_patterns": [...]}``.
    """
    global _last_detect_id

    if not PROCEDURAL_MEMORY_ENABLED:
        return {"detected": 0, "new_patterns": []}

    pending = get_action_count_since_last_detect()
    if pending < DETECT_THRESHOLD:
        return {"detected": 0, "new_patterns": [], "skipped": True, "pending": pending}

    with _lock:
        db = _conn()
        try:
            rows = db.execute(
                "SELECT id, session_id, action, params_json, result_summary, timestamp "
                "FROM action_log ORDER BY id DESC LIMIT ?",
                (SCAN_WINDOW,),
            ).fetchall()
            if rows:
                _last_detect_id = rows[0][0]
        finally:
            db.close()

    if not rows:
        return {"detected": 0, "new_patterns": []}

    # Format actions for the LLM prompt
    action_lines: list[str] = []
    for row in rows:
        _id, sid, act, pj, summary, ts = row
        action_lines.append(f"[{ts}] session={sid} action={act} params={pj} result={summary}")
    actions_text = "\n".join(action_lines)

    try:
        result = run_chat_fn(
            "remember_extract",
            [
                {"role": "system", "content": DETECT_PROMPT.format(actions=actions_text)},
                {"role": "user", "content": "Identify recurring workflow patterns from the actions above."},
            ],
            json_mode=True,
            max_tokens=1500,
        )
        data = json.loads(result["text"])
    except Exception as exc:
        logger.warning("Pattern detection LLM call failed: %s", exc)
        return {"detected": 0, "new_patterns": [], "error": str(exc)}

    patterns = data.get("patterns", [])
    new_patterns: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _conn()
        try:
            for p in patterns:
                trigger = p.get("trigger_pattern", "")
                steps = p.get("steps", [])
                freq = int(p.get("frequency", 1))
                if not trigger or not steps:
                    continue
                confidence = min(1.0, freq / 5.0)
                existing = db.execute(
                    "SELECT id, frequency FROM action_sequences WHERE trigger_pattern = ?",
                    (trigger,),
                ).fetchone()
                if existing:
                    new_freq = existing[1] + freq
                    new_conf = min(1.0, new_freq / 5.0)
                    db.execute(
                        "UPDATE action_sequences SET steps_json = ?, frequency = ?, "
                        "last_observed = ?, confidence = ? WHERE id = ?",
                        (json.dumps(steps), new_freq, now, new_conf, existing[0]),
                    )
                else:
                    db.execute(
                        "INSERT INTO action_sequences "
                        "(trigger_pattern, steps_json, frequency, last_observed, confidence, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (trigger, json.dumps(steps), freq, now, confidence, now),
                    )
                    new_patterns.append({"trigger_pattern": trigger, "steps": steps, "frequency": freq,
                                         "confidence": confidence})
            db.commit()
        finally:
            db.close()

    logger.info("Pattern detection complete: %d total, %d new", len(patterns), len(new_patterns))
    return {"detected": len(patterns), "new_patterns": new_patterns}


# ---------------------------------------------------------------------------
# Workflow matching
# ---------------------------------------------------------------------------

def match_workflow(
    query: str,
    qdrant_client: Any | None = None,
    embed_fn: Callable[..., list[float]] | None = None,
    threshold: float = 0.85,
) -> list[dict]:
    """Search for matching workflows using semantic similarity against trigger patterns.

    If *qdrant_client* and *embed_fn* are provided, uses vector search on the
    ``procedural_workflows`` collection.  Otherwise falls back to SQLite LIKE
    matching on ``trigger_pattern``.

    Returns matching workflows sorted by confidence (descending).
    """
    if not PROCEDURAL_MEMORY_ENABLED:
        return []

    # Vector search path
    if qdrant_client is not None and embed_fn is not None:
        try:
            query_vec = embed_fn(query)
            hits = qdrant_client.search(
                collection_name="procedural_workflows",
                query_vector=query_vec,
                limit=10,
                score_threshold=threshold,
            )
            results: list[dict] = []
            for hit in hits:
                payload = hit.payload or {}
                results.append({
                    "workflow_id": payload.get("workflow_id"),
                    "trigger": payload.get("trigger_pattern", ""),
                    "steps": payload.get("steps", []),
                    "confidence": payload.get("confidence", 0.0),
                    "frequency": payload.get("frequency", 0),
                    "score": hit.score,
                })
            results.sort(key=lambda r: r["confidence"], reverse=True)
            return results
        except Exception as exc:
            logger.warning("Qdrant workflow search failed, falling back to SQLite: %s", exc)

    # SQLite fallback — simple LIKE matching
    try:
        db = _conn()
        keywords = [f"%{w}%" for w in query.lower().split() if len(w) > 2]
        if not keywords:
            db.close()
            return []
        where_clauses = " AND ".join(["LOWER(trigger_pattern) LIKE ?"] * len(keywords))
        rows = db.execute(
            f"SELECT id, trigger_pattern, steps_json, frequency, confidence, last_observed, "  # noqa: S608
            f"last_executed, success_rate, auto_suggest "
            f"FROM action_sequences WHERE {where_clauses} ORDER BY confidence DESC LIMIT 10",
            keywords,
        ).fetchall()
        db.close()
    except Exception as exc:
        logger.warning("SQLite workflow search failed: %s", exc)
        return []

    results = []
    for row in rows:
        results.append({
            "workflow_id": row[0],
            "trigger": row[1],
            "steps": json.loads(row[2]),
            "frequency": row[3],
            "confidence": row[4],
            "last_observed": row[5],
            "last_executed": row[6],
            "success_rate": row[7],
            "auto_suggest": bool(row[8]),
        })
    return results


def suggest_workflow(
    query: str,
    qdrant_client: Any | None = None,
    embed_fn: Callable[..., list[float]] | None = None,
) -> dict | None:
    """Return a high-confidence workflow suggestion for *query*, or ``None``.

    Only returns a suggestion when the best match has confidence >=
    ``SUGGEST_CONFIDENCE``.
    """
    if not PROCEDURAL_MEMORY_ENABLED:
        return None

    matches = match_workflow(query, qdrant_client=qdrant_client, embed_fn=embed_fn)
    if not matches:
        return None

    best = matches[0]
    if best.get("confidence", 0.0) < SUGGEST_CONFIDENCE:
        return None

    return {
        "workflow_id": best["workflow_id"],
        "trigger": best["trigger"],
        "steps": best["steps"],
        "confidence": best["confidence"],
        "frequency": best["frequency"],
    }


# ---------------------------------------------------------------------------
# Workflow execution
# ---------------------------------------------------------------------------

def execute_workflow(workflow_id: int, execute_step_fn: Callable[..., dict]) -> dict:
    """Replay a learned workflow's steps.

    *execute_step_fn* is called for each step as
    ``execute_step_fn(action, params)`` and should return a dict with at least
    a ``"success"`` boolean key.

    Updates ``last_executed`` and ``success_rate`` on the workflow record.
    """
    with _lock:
        db = _conn()
        try:
            row = db.execute(
                "SELECT steps_json, success_rate, frequency FROM action_sequences WHERE id = ?",
                (workflow_id,),
            ).fetchone()
        finally:
            db.close()

    if not row:
        return {"success": False, "error": "Workflow not found", "workflow_id": workflow_id}

    steps = json.loads(row[0])
    prev_success_rate = row[1]
    frequency = row[2]

    step_results: list[dict] = []
    all_ok = True
    for step in steps:
        action = step.get("action", "")
        params = step.get("params", {})
        try:
            res = execute_step_fn(action, params)
            step_results.append({"action": action, "result": res, "success": res.get("success", True)})
            if not res.get("success", True):
                all_ok = False
        except Exception as exc:
            logger.warning("Workflow step '%s' failed: %s", action, exc)
            step_results.append({"action": action, "error": str(exc), "success": False})
            all_ok = False

    # Update success rate using exponential moving average
    current_outcome = 1.0 if all_ok else 0.0
    alpha = 1.0 / max(frequency, 1)
    new_success_rate = prev_success_rate * (1 - alpha) + current_outcome * alpha
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _conn()
        try:
            db.execute(
                "UPDATE action_sequences SET last_executed = ?, success_rate = ? WHERE id = ?",
                (now, new_success_rate, workflow_id),
            )
            db.commit()
        finally:
            db.close()

    return {
        "success": all_ok,
        "workflow_id": workflow_id,
        "steps_executed": len(step_results),
        "step_results": step_results,
        "success_rate": new_success_rate,
    }


# ---------------------------------------------------------------------------
# Management helpers
# ---------------------------------------------------------------------------

def get_workflows(limit: int = 50) -> list[dict]:
    """List all learned workflows ordered by frequency (descending)."""
    if not PROCEDURAL_MEMORY_ENABLED:
        return []
    try:
        db = _conn()
        rows = db.execute(
            "SELECT id, trigger_pattern, steps_json, frequency, confidence, "
            "last_observed, last_executed, success_rate, auto_suggest, created_at "
            "FROM action_sequences ORDER BY frequency DESC LIMIT ?",
            (limit,),
        ).fetchall()
        db.close()
    except Exception as exc:
        logger.warning("Failed to list workflows: %s", exc)
        return []

    return [
        {
            "workflow_id": r[0],
            "trigger": r[1],
            "steps": json.loads(r[2]),
            "frequency": r[3],
            "confidence": r[4],
            "last_observed": r[5],
            "last_executed": r[6],
            "success_rate": r[7],
            "auto_suggest": bool(r[8]),
            "created_at": r[9],
        }
        for r in rows
    ]


def toggle_auto_suggest(workflow_id: int, enabled: bool) -> dict:
    """Enable or disable auto-suggestion for a workflow."""
    with _lock:
        db = _conn()
        try:
            cur = db.execute(
                "UPDATE action_sequences SET auto_suggest = ? WHERE id = ?",
                (1 if enabled else 0, workflow_id),
            )
            db.commit()
            changed = cur.rowcount > 0
        finally:
            db.close()
    if not changed:
        return {"success": False, "error": "Workflow not found", "workflow_id": workflow_id}
    return {"success": True, "workflow_id": workflow_id, "auto_suggest": enabled}


def cleanup_old_actions(days: int = 30) -> int:
    """Remove action log entries older than *days*. Returns count deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _lock:
        db = _conn()
        try:
            cur = db.execute("DELETE FROM action_log WHERE timestamp < ?", (cutoff,))
            deleted = cur.rowcount
            db.commit()
        finally:
            db.close()
    logger.info("Cleaned up %d action log entries older than %d days", deleted, days)
    return deleted
