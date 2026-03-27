"""Migration 020 — github_sync_log and obsidian_index tables."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

logger = logging.getLogger("rag-bridge.migration_020")

VERSION = 20

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def _db_path() -> pathlib.Path:
    return pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")) / "scheduler.db"


def check(_ctx: dict) -> bool:
    """Return True only if BOTH github_sync_log AND obsidian_index tables exist."""
    db_file = _db_path()
    if not db_file.exists():
        return False
    try:
        db = sqlite3.connect(str(db_file))
        try:
            tables = {
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            return "github_sync_log" in tables and "obsidian_index" in tables
        finally:
            db.close()
    except Exception:  # pylint: disable=broad-except
        return False


def migrate(_ctx: dict) -> None:
    """Create github_sync_log and obsidian_index tables idempotently."""
    db_file = _db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_file))
    try:
        db.execute("PRAGMA journal_mode=WAL")

        db.execute("""
            CREATE TABLE IF NOT EXISTS github_sync_log (
                id                  TEXT PRIMARY KEY,
                synced_at           TEXT NOT NULL,
                repos_synced        TEXT NOT NULL,
                items_synced        INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL,
                error_message       TEXT,
                rate_limit_remaining INTEGER,
                rate_limit_reset    TEXT
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_github_sync_log_synced_at "
            "ON github_sync_log(synced_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_github_sync_log_status "
            "ON github_sync_log(status)"
        )

        db.execute("""
            CREATE TABLE IF NOT EXISTS obsidian_index (
                id              TEXT PRIMARY KEY,
                source_doc_id   TEXT NOT NULL,
                source_path     TEXT NOT NULL,
                target_note_name TEXT NOT NULL,
                created_at      TEXT NOT NULL
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_obsidian_index_source_doc_id "
            "ON obsidian_index(source_doc_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_obsidian_index_target_note_name "
            "ON obsidian_index(target_note_name)"
        )

        db.commit()
        logger.info("Migration 020: github_sync_log and obsidian_index tables created")
    finally:
        db.close()
