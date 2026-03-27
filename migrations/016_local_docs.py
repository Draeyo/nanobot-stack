"""016_local_docs — docs_ingestion_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 16

logger = logging.getLogger("migration.v16")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='docs_ingestion_log'"
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
            CREATE TABLE IF NOT EXISTS docs_ingestion_log (
                id            TEXT PRIMARY KEY,
                file_path     TEXT NOT NULL UNIQUE,
                file_hash     TEXT NOT NULL,
                file_type     TEXT NOT NULL,
                chunks_count  INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL,
                error_message TEXT,
                last_indexed  TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_status
            ON docs_ingestion_log(status);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_file_type
            ON docs_ingestion_log(file_type);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_last_indexed
            ON docs_ingestion_log(last_indexed);
        """)
        db.commit()
        logger.info("Migration 016: docs_ingestion_log table created at %s", db_path)
    finally:
        db.close()
