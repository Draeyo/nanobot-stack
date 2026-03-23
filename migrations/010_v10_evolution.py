"""v10 evolution — trust engine, procedural memory, token budget, knowledge graph enrichment.

Creates new SQLite databases and extends existing schemas for v10 features:
- Trust engine: per-action trust levels with auto-promotion
- Procedural memory: workflow learning from action patterns
- Token budget: cost tracking and enforcement
- Knowledge graph: new columns for confidence, source, aliases, temporal queries
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 10

logger = logging.getLogger("migration.v10")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def _safe_add_column(db: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    """Add a column if it doesn't exist (compatible with SQLite < 3.35)."""
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    except sqlite3.OperationalError:
        pass  # column already exists


def migrate(_ctx: dict) -> None:
    """Run v10 schema migrations."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # --- Trust Engine ---
    trust_db = sqlite3.connect(str(STATE_DIR / "trust.db"))
    trust_db.execute("PRAGMA journal_mode=WAL")
    trust_db.execute("""CREATE TABLE IF NOT EXISTS trust_policies (
        action_type TEXT PRIMARY KEY,
        trust_level TEXT NOT NULL DEFAULT 'approval_required',
        auto_promote_after INTEGER DEFAULT 0,
        successful_executions INTEGER DEFAULT 0,
        failed_executions INTEGER DEFAULT 0,
        last_promoted_at TEXT DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""")
    trust_db.execute("""CREATE TABLE IF NOT EXISTS trust_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL,
        action_detail TEXT NOT NULL,
        trust_level TEXT NOT NULL,
        outcome TEXT NOT NULL,
        rollback_info TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    # Seed default policies
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    default_policies = [
        ("shell_read", "auto"),
        ("shell_write", "approval_required"),
        ("web_fetch", "auto"),
        ("notify", "auto"),
        ("remember", "auto"),
        ("config_write", "approval_required"),
    ]
    for action_type, level in default_policies:
        trust_db.execute(
            "INSERT OR IGNORE INTO trust_policies (action_type, trust_level, updated_at) VALUES (?, ?, ?)",
            (action_type, level, now),
        )
    trust_db.commit()
    trust_db.close()
    logger.info("Trust engine database initialized")

    # --- Procedural Memory ---
    pm_db = sqlite3.connect(str(STATE_DIR / "procedural_memory.db"))
    pm_db.execute("PRAGMA journal_mode=WAL")
    pm_db.execute("""CREATE TABLE IF NOT EXISTS action_sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_pattern TEXT NOT NULL UNIQUE,
        trigger_embedding_id TEXT DEFAULT '',
        steps_json TEXT NOT NULL,
        frequency INTEGER DEFAULT 1,
        last_observed TEXT NOT NULL,
        last_executed TEXT DEFAULT '',
        success_rate REAL DEFAULT 1.0,
        confidence REAL DEFAULT 0.0,
        auto_suggest BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    pm_db.execute("""CREATE TABLE IF NOT EXISTS action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        action TEXT NOT NULL,
        params_json TEXT NOT NULL,
        result_summary TEXT DEFAULT '',
        timestamp TEXT NOT NULL
    )""")
    pm_db.execute("CREATE INDEX IF NOT EXISTS idx_action_log_session ON action_log(session_id)")
    pm_db.execute("CREATE INDEX IF NOT EXISTS idx_action_log_timestamp ON action_log(timestamp)")
    pm_db.commit()
    pm_db.close()
    logger.info("Procedural memory database initialized")

    # --- Token Budget ---
    tb_db = sqlite3.connect(str(STATE_DIR / "token_budgets.db"))
    tb_db.execute("PRAGMA journal_mode=WAL")
    tb_db.execute("""CREATE TABLE IF NOT EXISTS token_budgets (
        period TEXT PRIMARY KEY,
        budget_tokens INTEGER NOT NULL,
        used_tokens INTEGER DEFAULT 0,
        budget_cost_cents INTEGER DEFAULT 0,
        used_cost_cents INTEGER DEFAULT 0,
        reset_at TEXT NOT NULL
    )""")
    tb_db.execute("""CREATE TABLE IF NOT EXISTS token_usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT DEFAULT '',
        operation_type TEXT NOT NULL DEFAULT 'chat',
        task_type TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        estimated_cost_cents REAL NOT NULL,
        timestamp TEXT NOT NULL
    )""")
    tb_db.execute("CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON token_usage_log(timestamp)")
    tb_db.execute("CREATE INDEX IF NOT EXISTS idx_usage_model ON token_usage_log(model)")
    tb_db.commit()
    tb_db.close()
    logger.info("Token budget database initialized")

    # --- Knowledge Graph enrichment ---
    kg_path = STATE_DIR / "knowledge_graph.db"
    if kg_path.exists():
        kg_db = sqlite3.connect(str(kg_path))
        _safe_add_column(kg_db, "entities", "updated_at", "TEXT DEFAULT ''")
        _safe_add_column(kg_db, "entities", "confidence", "REAL DEFAULT 1.0")
        _safe_add_column(kg_db, "entities", "source", "TEXT DEFAULT 'conversation'")
        _safe_add_column(kg_db, "entities", "aliases", "TEXT DEFAULT '[]'")
        _safe_add_column(kg_db, "relations", "last_confirmed", "TEXT DEFAULT ''")
        _safe_add_column(kg_db, "relations", "source", "TEXT DEFAULT 'conversation'")
        _safe_add_column(kg_db, "relations", "confidence", "REAL DEFAULT 1.0")
        kg_db.commit()
        kg_db.close()
        logger.info("Knowledge graph schema enriched")

    logger.info("v10 migration complete")
