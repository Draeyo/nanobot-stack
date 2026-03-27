"""019_push_subscriptions — push_subscriptions table for Web Push VAPID."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 19

logger = logging.getLogger("migration.v19")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='push_subscriptions'"
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
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          TEXT PRIMARY KEY,
                endpoint    TEXT NOT NULL UNIQUE,
                p256dh      TEXT NOT NULL,
                auth        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                last_used   TEXT DEFAULT NULL
            );
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_subscriptions_endpoint
            ON push_subscriptions (endpoint);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_subscriptions_created_at
            ON push_subscriptions (created_at);
        """)
        db.commit()
        logger.info("Migration 019: push_subscriptions table created at %s", db_path)
    finally:
        db.close()
