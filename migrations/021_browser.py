"""021_browser — browser_action_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 21

logger = logging.getLogger("migration.v21")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    """Return True if browser_action_log table exists in browser.db."""
    db_path = STATE_DIR / "browser.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_action_log'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    """Create browser_action_log table in browser.db."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS browser_action_log (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                action_type TEXT NOT NULL,
                url         TEXT NOT NULL,
                selector    TEXT,
                status      TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                approved_by TEXT,
                started_at  TEXT NOT NULL,
                duration_ms INTEGER,
                error_msg   TEXT
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_session_id
            ON browser_action_log(session_id)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_started_at
            ON browser_action_log(started_at DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_status
            ON browser_action_log(status)
        """)
        db.commit()
        logger.info("Migration 021: browser_action_log table created at %s", db_path)
    finally:
        db.close()
