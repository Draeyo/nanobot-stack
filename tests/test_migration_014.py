"""Tests for migration 016 — docs_ingestion_log table."""
import os
import sqlite3
import tempfile
import pathlib
import pytest

os.environ.setdefault("RAG_STATE_DIR", tempfile.mkdtemp())

import importlib
import sys


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        pathlib.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_returns_false_before_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    assert mod.check({}) is False


def test_migrate_creates_table(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='docs_ingestion_log'"
    ).fetchall()
    db.close()
    assert len(tables) == 1


def test_migrate_creates_indexes(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    indexes = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    db.close()
    assert "idx_docs_log_status" in indexes
    assert "idx_docs_log_file_type" in indexes
    assert "idx_docs_log_last_indexed" in indexes


def test_check_returns_true_after_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    assert mod.check({}) is True


def test_migrate_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    mod.migrate({})  # second call must not raise
    assert mod.check({}) is True
