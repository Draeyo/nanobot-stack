"""015_backup_log — backup_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 15

logger = logging.getLogger("migration.v15")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backup_log'"
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
            CREATE TABLE IF NOT EXISTS backup_log (
                id              TEXT PRIMARY KEY,
                started_at      TEXT NOT NULL,
                completed_at    TEXT,
                archive_path    TEXT,
                archive_s3_key  TEXT,
                size_bytes      INTEGER DEFAULT 0,
                collections_count INTEGER DEFAULT 0,
                sqlite_files_count INTEGER DEFAULT 0,
                encrypted       INTEGER DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'running',
                error_msg       TEXT
            );
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_backup_log_started_at
            ON backup_log(started_at DESC);
        """)
        db.commit()
        logger.info("Migration 015: backup_log table created at %s", db_path)
    finally:
        db.close()
