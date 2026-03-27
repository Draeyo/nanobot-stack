"""014_web_search — web_search_log table in scheduler.db."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 14

logger = logging.getLogger("migration.v14")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='web_search_log'"
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
            CREATE TABLE IF NOT EXISTS web_search_log (
                id              TEXT PRIMARY KEY,
                query           TEXT NOT NULL,
                categories      TEXT NOT NULL DEFAULT '[]',
                num_results     INTEGER NOT NULL DEFAULT 5,
                results_stored  INTEGER NOT NULL DEFAULT 0,
                duration_ms     INTEGER,
                status          TEXT NOT NULL,
                error_message   TEXT,
                source          TEXT NOT NULL DEFAULT 'api',
                created_at      TEXT NOT NULL
            );
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_search_log_created_at "
            "ON web_search_log(created_at);"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_search_log_status "
            "ON web_search_log(status);"
        )
        db.commit()
        logger.info("Migration 014: web_search_log table created at %s", db_path)
    finally:
        db.close()
