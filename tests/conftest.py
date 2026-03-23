"""Shared fixtures for nanobot-stack tests."""
from __future__ import annotations
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Temporary SQLite database for scheduler tests."""
    db_path = tmp_path / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE scheduled_jobs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, cron TEXT NOT NULL,
            prompt TEXT NOT NULL DEFAULT '', sections TEXT NOT NULL DEFAULT '[]',
            channels TEXT NOT NULL DEFAULT '[]', enabled INTEGER NOT NULL DEFAULT 1,
            system INTEGER NOT NULL DEFAULT 0, timeout_s INTEGER NOT NULL DEFAULT 60,
            last_run TEXT, last_status TEXT, last_output TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE job_runs (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, started_at TEXT NOT NULL,
            duration_ms INTEGER, status TEXT NOT NULL,
            output TEXT, error TEXT, channels_ok TEXT
        )
    """)
    db.commit()
    db.close()
    return db_path


@pytest.fixture
def mock_send_notification():
    with patch("tools.send_notification", new_callable=AsyncMock) as m:
        m.return_value = {"ok": True, "service": "ntfy"}
        yield m


@pytest.fixture
def mock_dm_pairing():
    with patch("dm_pairing.list_approved_users") as m:
        m.return_value = [
            {"platform_id": "telegram:111", "display_name": "Alice", "approved_at": "", "approved_by": "admin"},
            {"platform_id": "discord:222", "display_name": "Bob", "approved_at": "", "approved_by": "admin"},
        ]
        yield m


@pytest.fixture
def mock_channel_manager():
    mgr = MagicMock()
    mgr.get_adapter = MagicMock(return_value=AsyncMock())
    mgr.get_adapter.return_value.send_message = AsyncMock(return_value={"ok": True})
    return mgr
