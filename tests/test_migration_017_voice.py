"""Tests for migration 017 — voice_sessions table."""
import os
import sqlite3
import tempfile
import importlib
import sys
from pathlib import Path
import pytest


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    # Force re-import so STATE_DIR picks up the monkeypatched env var
    if "migration_017_voice" in sys.modules:
        del sys.modules["migration_017_voice"]
    yield tmp_path


def _import_migration(tmp_state):
    spec = importlib.util.spec_from_file_location(
        "migration_017_voice",
        str(Path(__file__).resolve().parent.parent / "migrations" / "017_voice.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migrate_creates_table(tmp_state):
    mod = _import_migration(tmp_state)
    assert not mod.check({})
    mod.migrate({})
    db = sqlite3.connect(str(tmp_state / "scheduler.db"))
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='voice_sessions'"
    ).fetchall()]
    assert "voice_sessions" in tables
    db.close()


def test_migrate_idempotent(tmp_state):
    mod = _import_migration(tmp_state)
    mod.migrate({})
    mod.migrate({})  # second call must not raise
    assert mod.check({})


def test_check_returns_false_before_migration(tmp_state):
    mod = _import_migration(tmp_state)
    assert mod.check({}) is False


def test_check_returns_true_after_migration(tmp_state):
    mod = _import_migration(tmp_state)
    mod.migrate({})
    assert mod.check({}) is True


def test_schema_columns(tmp_state):
    mod = _import_migration(tmp_state)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_state / "scheduler.db"))
    cols = {row[1] for row in db.execute("PRAGMA table_info(voice_sessions)")}
    expected = {
        "id", "started_at", "audio_duration_s", "transcription_chars",
        "tts_chars", "model_stt", "model_tts", "latency_ms", "status",
    }
    assert expected == cols
    db.close()


def test_index_created(tmp_state):
    mod = _import_migration(tmp_state)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_state / "scheduler.db"))
    indexes = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND tbl_name='voice_sessions'"
    ).fetchall()]
    assert "idx_voice_sessions_started_at" in indexes
    db.close()
