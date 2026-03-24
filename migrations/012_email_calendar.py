"""012_email_calendar — email_sync_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 12

logger = logging.getLogger("migration.v12")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='email_sync_log'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS email_sync_log (
                id          TEXT PRIMARY KEY,
                account     TEXT NOT NULL,
                last_synced TEXT NOT NULL,
                items_synced INTEGER DEFAULT 0,
                status      TEXT NOT NULL
            );
        """)
        db.commit()
        logger.info("Migration 012: email_sync_log table created at %s", db_path)
    finally:
        db.close()
