"""Tests for browser API endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


def _make_test_app():
    from fastapi import FastAPI
    import importlib
    if "browser_api" in sys.modules:
        del sys.modules["browser_api"]
    browser_api = importlib.import_module("browser_api")
    app = FastAPI()
    app.include_router(browser_api.router)
    return app, browser_api


def test_browser_run_endpoint_disabled(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "false")
    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.post(
        "/api/browser/run",
        json={"task": "navigate to example.com"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code in (200, 503)
    body = response.json()
    if response.status_code == 200:
        assert body.get("status") == "disabled"


def test_browser_run_endpoint_returns_result(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    app, browser_api = _make_test_app()
    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.output = "Navigated to example.com"
    mock_result.actions_taken = []
    mock_result.cost_tokens = 0
    mock_result.artifacts = {}

    async def mock_run(task, context=None):
        return mock_result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    with patch.object(browser_api, "_get_browser_agent", return_value=mock_agent):
        client = TestClient(app)
        response = client.post(
            "/api/browser/run",
            json={"task": "navigate to example.com"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"


def test_browser_action_log_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    import sqlite3
    db_path = tmp_path / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""CREATE TABLE browser_action_log (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action_type TEXT NOT NULL,
        url TEXT NOT NULL, selector TEXT, status TEXT NOT NULL, trust_level TEXT NOT NULL,
        approved_by TEXT, started_at TEXT NOT NULL, duration_ms INTEGER, error_msg TEXT
    )""")
    db.execute(
        "INSERT INTO browser_action_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("id-1", "sess-1", "navigate", "https://example.com", None, "ok", "auto", "auto",
         "2026-01-01T00:00:00Z", 100, None),
    )
    db.commit()
    db.close()
    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.get("/api/browser/action-log")
    assert response.status_code == 200
    body = response.json()
    assert "entries" in body
    assert len(body["entries"]) >= 1


def test_browser_sessions_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    import sqlite3
    db_path = tmp_path / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""CREATE TABLE browser_action_log (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action_type TEXT NOT NULL,
        url TEXT NOT NULL, selector TEXT, status TEXT NOT NULL, trust_level TEXT NOT NULL,
        approved_by TEXT, started_at TEXT NOT NULL, duration_ms INTEGER, error_msg TEXT
    )""")
    db.execute(
        "INSERT INTO browser_action_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("id-1", "sess-abc", "navigate", "https://example.com", None, "ok", "auto", "auto",
         "2026-01-01T00:00:00Z", 100, None),
    )
    db.commit()
    db.close()
    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.get("/api/browser/sessions")
    assert response.status_code == 200
    body = response.json()
    assert "sessions" in body


def test_browser_router_mounted_in_app():
    app_path = Path(__file__).parent.parent / "src" / "bridge" / "app.py"
    content = app_path.read_text()
    assert "browser_api" in content or "browser_router" in content, \
        "browser_api not imported in app.py"
    assert "/api/browser" in content or "browser_router" in content, \
        "browser router not mounted in app.py"
