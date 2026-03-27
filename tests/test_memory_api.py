"""Tests for memory_api REST endpoints."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


@pytest.fixture()
def app_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DECAY_ENABLED", "true")
    monkeypatch.setenv("MEMORY_DECAY_LAMBDA", "0.01")
    monkeypatch.setenv("MEMORY_DECAY_THRESHOLD", "0.1")
    monkeypatch.setenv("MEMORY_DECAY_COLLECTIONS", "memory_personal")
    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))

    db = sqlite3.connect(str(tmp_path / "feedback.db"))
    db.execute("""CREATE TABLE IF NOT EXISTS memory_decay_log (
        id TEXT PRIMARY KEY, collection TEXT NOT NULL, point_id TEXT NOT NULL,
        old_score REAL NOT NULL, new_score REAL NOT NULL,
        reason TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS routing_adjustments (
        query_type TEXT NOT NULL, model_id TEXT NOT NULL,
        adjustment REAL NOT NULL DEFAULT 1.0,
        feedback_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY (query_type, model_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS feedback (
        chunk_id TEXT NOT NULL, collection TEXT NOT NULL,
        query TEXT NOT NULL, signal TEXT NOT NULL, created_at TEXT NOT NULL,
        query_type TEXT DEFAULT NULL, model_used TEXT DEFAULT NULL,
        was_helpful INTEGER DEFAULT NULL, correction_text TEXT DEFAULT NULL,
        PRIMARY KEY (chunk_id, query, signal)
    )""")
    db.commit()
    db.close()

    mock_qdrant = MagicMock()
    mock_qdrant.scroll.return_value = ([], None)

    app = FastAPI()
    import importlib
    import memory_decay
    importlib.reload(memory_decay)
    import feedback_learner
    importlib.reload(feedback_learner)
    import memory_api
    importlib.reload(memory_api)
    memory_api.init_memory_api(qdrant_client=mock_qdrant)
    app.include_router(memory_api.memory_router)
    app.include_router(memory_api.feedback_router)
    return TestClient(app), tmp_path, mock_qdrant


class TestMemoryDecayEndpoints:

    def test_decay_run_returns_200_when_enabled(self, app_and_db):
        client, _, _ = app_and_db
        r = client.post("/api/memory/decay/run")
        assert r.status_code == 200
        assert "scanned" in r.json()

    def test_decay_log_returns_200(self, app_and_db):
        client, _, _ = app_and_db
        r = client.get("/api/memory/decay/log")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_decay_log_pagination(self, app_and_db):
        client, tmp_path, _ = app_and_db
        # Insert 5 log entries
        from datetime import datetime, timezone
        import uuid
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        for i in range(5):
            db.execute(
                "INSERT INTO memory_decay_log VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "memory_personal", f"pt-{i}",
                 0.9, 0.8, "decay", datetime.now(timezone.utc).isoformat())
            )
        db.commit()
        db.close()
        r = client.get("/api/memory/decay/log?limit=3&offset=0")
        assert r.status_code == 200
        assert len(r.json()) <= 3

    def test_forget_returns_200(self, app_and_db):
        client, _, mock_qdrant = app_and_db
        mock_qdrant.retrieve.return_value = [MagicMock(payload={"confidence_score": 0.5})]
        r = client.post("/api/memory/forget", json={"collection": "memory_personal", "point_id": "pt-x"})
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_health_returns_200(self, app_and_db):
        client, _, _ = app_and_db
        r = client.get("/api/memory/health")
        assert r.status_code == 200

    def test_decay_run_returns_503_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", "false")
        monkeypatch.setenv("MEMORY_DECAY_LAMBDA", "0.01")
        monkeypatch.setenv("MEMORY_DECAY_THRESHOLD", "0.1")
        monkeypatch.setenv("MEMORY_DECAY_COLLECTIONS", "memory_personal")
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))

        # Create required tables
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        db.execute("""CREATE TABLE IF NOT EXISTS memory_decay_log (
            id TEXT PRIMARY KEY, collection TEXT NOT NULL, point_id TEXT NOT NULL,
            old_score REAL NOT NULL, new_score REAL NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS routing_adjustments (
            query_type TEXT NOT NULL, model_id TEXT NOT NULL,
            adjustment REAL NOT NULL DEFAULT 1.0,
            feedback_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL, PRIMARY KEY (query_type, model_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback (
            chunk_id TEXT NOT NULL, collection TEXT NOT NULL,
            query TEXT NOT NULL, signal TEXT NOT NULL, created_at TEXT NOT NULL,
            query_type TEXT DEFAULT NULL, model_used TEXT DEFAULT NULL,
            was_helpful INTEGER DEFAULT NULL, correction_text TEXT DEFAULT NULL,
            PRIMARY KEY (chunk_id, query, signal)
        )""")
        db.commit()
        db.close()

        app = FastAPI()
        import importlib
        import memory_decay
        importlib.reload(memory_decay)
        import feedback_learner
        importlib.reload(feedback_learner)
        import memory_api
        importlib.reload(memory_api)
        memory_api.init_memory_api(qdrant_client=MagicMock())
        app.include_router(memory_api.memory_router)
        client = TestClient(app)
        r = client.post("/api/memory/decay/run")
        assert r.status_code == 503


class TestFeedbackEndpoints:

    def test_post_feedback_returns_201(self, app_and_db):
        client, tmp_path, _ = app_and_db
        # Insert a feedback row to reference
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        from datetime import datetime, timezone
        db.execute(
            "INSERT INTO feedback (chunk_id, collection, query, signal, created_at) VALUES (?,?,?,?,?)",
            ("qid-test", "col", "q", "positive", datetime.now(timezone.utc).isoformat())
        )
        db.commit()
        db.close()
        r = client.post("/api/feedback/", json={
            "query_id": "qid-test", "query_type": "code",
            "model_used": "gpt-4o-mini", "was_helpful": False, "correction_text": "bad"
        })
        assert r.status_code == 201

    def test_post_feedback_returns_404_unknown_id(self, app_and_db):
        client, _, _ = app_and_db
        r = client.post("/api/feedback/", json={
            "query_id": "no-such-id", "query_type": "code",
            "model_used": "gpt-4o-mini", "was_helpful": False
        })
        assert r.status_code == 404

    def test_get_adjustments_returns_200(self, app_and_db):
        client, _, _ = app_and_db
        r = client.get("/api/feedback/adjustments")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_delete_adjustment_resets_to_1(self, app_and_db):
        client, tmp_path, _ = app_and_db
        from datetime import datetime, timezone
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        db.execute(
            "INSERT INTO routing_adjustments VALUES (?,?,?,?,?)",
            ("code", "gpt-4o-mini", 0.7, 3, datetime.now(timezone.utc).isoformat())
        )
        db.commit()
        db.close()
        r = client.delete("/api/feedback/adjustments/code/gpt-4o-mini")
        assert r.status_code == 200
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        row = db.execute(
            "SELECT adjustment FROM routing_adjustments WHERE query_type=? AND model_id=?",
            ("code", "gpt-4o-mini")
        ).fetchone()
        db.close()
        assert row is None or abs(row[0] - 1.0) < 0.001

    def test_get_summary_returns_200(self, app_and_db):
        client, _, _ = app_and_db
        r = client.get("/api/feedback/summary")
        assert r.status_code == 200
