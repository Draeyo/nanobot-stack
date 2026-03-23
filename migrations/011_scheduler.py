"""011_scheduler — scheduled_jobs and job_runs tables."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 11

logger = logging.getLogger("migration.v11")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('scheduled_jobs','job_runs')"
        ).fetchall()}
        return tables == {"scheduled_jobs", "job_runs"}
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                cron        TEXT NOT NULL,
                prompt      TEXT NOT NULL DEFAULT '',
                sections    TEXT NOT NULL DEFAULT '[]',
                channels    TEXT NOT NULL DEFAULT '[]',
                enabled     INTEGER NOT NULL DEFAULT 1,
                system      INTEGER NOT NULL DEFAULT 0,
                timeout_s   INTEGER NOT NULL DEFAULT 60,
                last_run    TEXT,
                last_status TEXT,
                last_output TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id          TEXT PRIMARY KEY,
                job_id      TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                duration_ms INTEGER,
                status      TEXT NOT NULL,
                output      TEXT,
                error       TEXT,
                channels_ok TEXT
            )
        """)
        db.commit()
        logger.info("Migration 011: scheduler tables created at %s", db_path)
    finally:
        db.close()
