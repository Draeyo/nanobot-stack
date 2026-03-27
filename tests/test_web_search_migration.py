"""Tests for migration 014 — web_search_log table."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


def _load_migration(tmp_path):
    migration_path = Path(__file__).parent.parent / "migrations" / "014_web_search.py"
    spec = importlib.util.spec_from_file_location("migration_014", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.STATE_DIR = tmp_path
    return mod


class TestMigration014:
    def test_migrate_creates_web_search_log(self, tmp_path):
        """014_web_search.migrate() creates web_search_log in scheduler.db."""
        mod = _load_migration(tmp_path)
        mod.migrate({})

        db_path = tmp_path / "scheduler.db"
        assert db_path.exists()
        db = sqlite3.connect(str(db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        assert "web_search_log" in tables

    def test_migrate_creates_indexes(self, tmp_path):
        """014_web_search.migrate() creates both required indexes."""
        mod = _load_migration(tmp_path)
        mod.migrate({})

        db_path = tmp_path / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        db.close()
        assert "idx_web_search_log_created_at" in indexes
        assert "idx_web_search_log_status" in indexes

    def test_check_returns_true_after_migrate(self, tmp_path):
        """check() returns True once migration has run."""
        mod = _load_migration(tmp_path)
        mod.migrate({})
        assert mod.check({}) is True

    def test_check_returns_false_when_db_missing(self, tmp_path):
        """check() returns False when scheduler.db does not exist."""
        mod = _load_migration(tmp_path)
        mod.STATE_DIR = tmp_path / "nonexistent"
        assert mod.check({}) is False

    def test_migrate_is_idempotent(self, tmp_path):
        """Running migrate() twice does not raise."""
        mod = _load_migration(tmp_path)
        mod.migrate({})
        mod.migrate({})  # second call must not raise

    def test_table_schema_has_required_columns(self, tmp_path):
        """web_search_log has all columns specified in the spec."""
        mod = _load_migration(tmp_path)
        mod.migrate({})

        db_path = tmp_path / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        cols = {row[1] for row in db.execute(
            "PRAGMA table_info(web_search_log)"
        ).fetchall()}
        db.close()
        expected = {
            "id", "query", "categories", "num_results",
            "results_stored", "duration_ms", "status",
            "error_message", "source", "created_at",
        }
        assert expected.issubset(cols)
