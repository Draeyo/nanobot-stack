"""Tests for migration 018 — memory_decay_log, routing_adjustments, feedback extension."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))
sys.path.insert(0, str(Path(__file__).parent.parent / "migrations"))


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    # Pre-create feedback.db with existing feedback table (as feedback.py would)
    db = sqlite3.connect(str(tmp_path / "feedback.db"))
    db.execute("""CREATE TABLE feedback (
        chunk_id   TEXT NOT NULL,
        collection TEXT NOT NULL,
        query      TEXT NOT NULL,
        signal     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chunk_id, query, signal)
    )""")
    db.commit()
    db.close()
    return tmp_path


def _load_migration(state_dir):
    """Load (or reload) the migration module, patching STATE_DIR."""
    import importlib
    try:
        m = importlib.import_module("018_memory_decay_feedback")
        importlib.reload(m)
    except ModuleNotFoundError:
        m = importlib.import_module("018_memory_decay_feedback")
    return m


def test_check_returns_false_before_migration(state_dir):
    m = _load_migration(state_dir)
    assert m.check({}) is False


def test_migrate_creates_memory_decay_log(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "memory_decay_log" in tables
    db.close()


def test_migrate_creates_routing_adjustments(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "routing_adjustments" in tables
    db.close()


def test_migrate_adds_feedback_columns(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(feedback)").fetchall()}
    assert "query_type" in cols
    assert "model_used" in cols
    assert "was_helpful" in cols
    assert "correction_text" in cols
    db.close()


def test_check_returns_true_after_migration(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    assert m.check({}) is True


def test_migrate_is_idempotent(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    m.migrate({})  # second run must not raise


def test_memory_decay_log_schema(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(memory_decay_log)").fetchall()}
    assert cols == {"id", "collection", "point_id", "old_score", "new_score", "reason", "created_at"}
    db.close()


def test_routing_adjustments_schema(state_dir):
    m = _load_migration(state_dir)
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(routing_adjustments)").fetchall()}
    assert cols == {"query_type", "model_id", "adjustment", "feedback_count", "updated_at"}
    db.close()
