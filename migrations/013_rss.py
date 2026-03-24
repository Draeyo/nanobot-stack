"""013_rss — rss_feeds and rss_entries tables in rss.db."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 13

logger = logging.getLogger("migration.v13")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "rss.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('rss_feeds','rss_entries')"
        ).fetchall()}
        return tables == {"rss_feeds", "rss_entries"}
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "rss.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rss_feeds (
                id                      TEXT PRIMARY KEY,
                url                     TEXT NOT NULL UNIQUE,
                name                    TEXT NOT NULL,
                category                TEXT NOT NULL DEFAULT 'general',
                refresh_interval_min    INTEGER NOT NULL DEFAULT 60,
                last_fetched            TEXT,
                last_status             TEXT,
                article_count           INTEGER DEFAULT 0,
                enabled                 INTEGER NOT NULL DEFAULT 1,
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS rss_entries (
                id          TEXT PRIMARY KEY,
                feed_id     TEXT NOT NULL,
                entry_id    TEXT NOT NULL UNIQUE,
                url         TEXT NOT NULL,
                title       TEXT NOT NULL,
                published_at TEXT,
                embedded    INTEGER NOT NULL DEFAULT 0,
                summarized  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        db.commit()
        logger.info("Migration 013: rss_feeds and rss_entries tables created at %s", db_path)
    finally:
        db.close()
