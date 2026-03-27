"""018_memory_decay_feedback — memory decay log, routing adjustments, feedback extension."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 18

logger = logging.getLogger("migration.v18")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def _safe_add_column(db: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    except sqlite3.OperationalError:
        pass  # column already exists


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "feedback.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        return "memory_decay_log" in tables and "routing_adjustments" in tables
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "feedback.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        # Extend existing feedback table
        _safe_add_column(db, "feedback", "query_type",      "TEXT DEFAULT NULL")
        _safe_add_column(db, "feedback", "model_used",      "TEXT DEFAULT NULL")
        _safe_add_column(db, "feedback", "was_helpful",     "INTEGER DEFAULT NULL")
        _safe_add_column(db, "feedback", "correction_text", "TEXT DEFAULT NULL")

        # New table: memory_decay_log (append-only audit trail)
        db.execute("""
            CREATE TABLE IF NOT EXISTS memory_decay_log (
                id           TEXT PRIMARY KEY,
                collection   TEXT NOT NULL,
                point_id     TEXT NOT NULL,
                old_score    REAL NOT NULL,
                new_score    REAL NOT NULL,
                reason       TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_decay_log_collection
            ON memory_decay_log(collection)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_decay_log_created_at
            ON memory_decay_log(created_at)
        """)

        # New table: routing_adjustments
        db.execute("""
            CREATE TABLE IF NOT EXISTS routing_adjustments (
                query_type      TEXT NOT NULL,
                model_id        TEXT NOT NULL,
                adjustment      REAL NOT NULL DEFAULT 1.0,
                feedback_count  INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (query_type, model_id)
            )
        """)

        db.commit()
        logger.info("Migration 018: memory_decay_log, routing_adjustments created; feedback extended at %s", db_path)
    finally:
        db.close()
