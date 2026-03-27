"""Tests for migration 021 — browser_action_log table."""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
sys.path.insert(0, str(MIGRATIONS_DIR))


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    return tmp_path


def _load():
    if "021_browser" in sys.modules:
        del sys.modules["021_browser"]
    return importlib.import_module("021_browser")


def test_version(state_dir):
    m = _load()
    assert m.VERSION == 21


def test_check_returns_false_before_migration(state_dir):
    m = _load()
    assert m.check({}) is False


def test_migrate_creates_table(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_action_log'"
    ).fetchall()
    db.close()
    assert len(tables) == 1


def test_migrate_creates_indexes(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    indexes = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='browser_action_log'"
    ).fetchall()
    db.close()
    index_names = {r[0] for r in indexes}
    assert "idx_browser_action_log_session_id" in index_names
    assert "idx_browser_action_log_started_at" in index_names
    assert "idx_browser_action_log_status" in index_names


def test_check_returns_true_after_migration(state_dir):
    m = _load()
    m.migrate({})
    assert m.check({}) is True


def test_migrate_is_idempotent(state_dir):
    m = _load()
    m.migrate({})
    m.migrate({})
    assert m.check({}) is True


def test_table_columns(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    cols = {row[1] for row in db.execute("PRAGMA table_info(browser_action_log)").fetchall()}
    db.close()
    expected = {
        "id", "session_id", "action_type", "url", "selector",
        "status", "trust_level", "approved_by", "started_at",
        "duration_ms", "error_msg",
    }
    assert expected.issubset(cols)


def test_insert_and_read(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    db.execute(
        "INSERT INTO browser_action_log "
        "(id, session_id, action_type, url, status, trust_level, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("uuid-1", "sess-1", "navigate", "https://example.com", "ok", "auto", "2026-01-01T00:00:00Z"),
    )
    db.commit()
    row = db.execute("SELECT action_type FROM browser_action_log WHERE id='uuid-1'").fetchone()
    db.close()
    assert row[0] == "navigate"
