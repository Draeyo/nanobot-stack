"""017_voice — voice_sessions metrics table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 17

logger = logging.getLogger("migration.v17")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='voice_sessions'"
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
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id                  TEXT PRIMARY KEY,
                started_at          TEXT NOT NULL,
                audio_duration_s    REAL NOT NULL DEFAULT 0.0,
                transcription_chars INTEGER NOT NULL DEFAULT 0,
                tts_chars           INTEGER NOT NULL DEFAULT 0,
                model_stt           TEXT NOT NULL,
                model_tts           TEXT NOT NULL,
                latency_ms          INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL DEFAULT 'ok'
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_voice_sessions_started_at
            ON voice_sessions (started_at)
        """)
        db.commit()
        logger.info("Migration 017: voice_sessions table created at %s", db_path)
    finally:
        db.close()
