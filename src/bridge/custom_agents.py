"""Custom sub-agents — user-created specialized agents persisted in SQLite.

Users can create sub-agents from the admin UI with:
  - A name and description
  - Custom system prompt / instructions
  - Optional forced model (overrides adaptive routing)
  - Enable/disable toggle

The orchestrator can invoke these automatically or on user request.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.custom_agents")

STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
DB_PATH = STATE_DIR / "custom_agents.db"

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_agents (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            forced_model TEXT NOT NULL DEFAULT '',
            tools       TEXT NOT NULL DEFAULT '[]',
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def list_custom_agents() -> list[dict[str, Any]]:
    with _lock:
        conn = _db()
        rows = conn.execute("SELECT * FROM custom_agents ORDER BY name").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_custom_agent(agent_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _db()
        row = conn.execute("SELECT * FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def get_custom_agent_by_name(name: str) -> dict[str, Any] | None:
    with _lock:
        conn = _db()
        row = conn.execute("SELECT * FROM custom_agents WHERE name=? AND enabled=1", (name,)).fetchone()
        conn.close()
    return dict(row) if row else None


def create_custom_agent(
    name: str,
    description: str = "",
    system_prompt: str = "",
    forced_model: str = "",
    tools: list[str] | None = None,
) -> dict[str, Any]:
    agent_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO custom_agents (id, name, description, system_prompt, forced_model, tools, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (agent_id, name.strip(), description, system_prompt, forced_model,
             json.dumps(tools or []), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
        conn.close()
    return dict(row)


_UPDATABLE_COLUMNS = frozenset({"name", "description", "system_prompt", "forced_model", "tools", "enabled"})

def update_custom_agent(agent_id: str, **fields) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _UPDATABLE_COLUMNS}
    if not updates:
        return get_custom_agent(agent_id)
    if "tools" in updates and isinstance(updates["tools"], list):
        updates["tools"] = json.dumps(updates["tools"])
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Build SET clause from hardcoded column names only (no user input in SQL)
    columns = [c for c in updates if c in _UPDATABLE_COLUMNS or c == "updated_at"]
    set_parts = []
    values = []
    for col in columns:
        set_parts.append(
            {"name": "name=?", "description": "description=?", "system_prompt": "system_prompt=?",
             "forced_model": "forced_model=?", "tools": "tools=?", "enabled": "enabled=?",
             "updated_at": "updated_at=?"}[col]
        )
        values.append(updates[col])
    values.append(agent_id)
    sql = "UPDATE custom_agents SET " + ", ".join(set_parts) + " WHERE id=?"
    with _lock:
        conn = _db()
        conn.execute(sql, values)
        conn.commit()
        row = conn.execute("SELECT * FROM custom_agents WHERE id=?", (agent_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def delete_custom_agent(agent_id: str) -> bool:
    with _lock:
        conn = _db()
        cur = conn.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,))
        conn.commit()
        conn.close()
    return cur.rowcount > 0
