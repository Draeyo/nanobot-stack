"""Tests for migration 020 — github_sync_log and obsidian_index tables."""
from __future__ import annotations
import importlib.util
import os
import pathlib
import sqlite3
import tempfile

import pytest


def _load_migration(tmp_path):
    os.environ["RAG_STATE_DIR"] = str(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "migration_020",
        pathlib.Path(__file__).parent.parent / "migrations" / "020_github_obsidian.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_returns_false_before_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    assert mod.check({}) is False


def test_migrate_creates_github_sync_log(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "github_sync_log" in tables
    finally:
        db.close()


def test_migrate_creates_obsidian_index(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "obsidian_index" in tables
    finally:
        db.close()


def test_github_sync_log_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        cols = {row[1] for row in db.execute(
            "PRAGMA table_info(github_sync_log)"
        ).fetchall()}
        assert cols >= {
            "id", "synced_at", "repos_synced", "items_synced",
            "status", "error_message", "rate_limit_remaining", "rate_limit_reset"
        }
    finally:
        db.close()


def test_obsidian_index_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        cols = {row[1] for row in db.execute(
            "PRAGMA table_info(obsidian_index)"
        ).fetchall()}
        assert cols >= {"id", "source_doc_id", "source_path", "target_note_name", "created_at"}
    finally:
        db.close()


def test_check_returns_true_after_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    assert mod.check({}) is True


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    mod.migrate({})  # second call must not raise


def test_github_sync_log_indexes_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_github_sync_log_synced_at" in indexes
        assert "idx_github_sync_log_status" in indexes
    finally:
        db.close()


def test_obsidian_index_indexes_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_obsidian_index_source_doc_id" in indexes
        assert "idx_obsidian_index_target_note_name" in indexes
    finally:
        db.close()
