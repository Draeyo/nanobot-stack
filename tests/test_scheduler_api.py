"""Tests for scheduler REST API."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from fastapi.testclient import TestClient  # pylint: disable=wrong-import-position
from fastapi import FastAPI  # pylint: disable=wrong-import-position


def _make_app(mgr):
    app = FastAPI()
    import scheduler_api
    scheduler_api.init_scheduler_api(manager=mgr, verify_token_dep=None)
    app.include_router(scheduler_api.router)
    return TestClient(app)


class TestSchedulerApiCRUD:
    def test_list_jobs_returns_200(self):
        mgr = MagicMock()
        mgr.list_jobs.return_value = []
        client = _make_app(mgr)
        r = client.get("/api/scheduler/jobs")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_job_validates_cron(self):
        mgr = MagicMock()
        client = _make_app(mgr)
        r = client.post("/api/scheduler/jobs", json={
            "name": "Test", "cron": "not-a-cron",
            "sections": ["system_health"], "channels": ["ntfy"]
        })
        assert r.status_code == 422
        mgr.create_job.assert_not_called()

    def test_create_job_rejects_invalid_section(self):
        mgr = MagicMock()
        client = _make_app(mgr)
        r = client.post("/api/scheduler/jobs", json={
            "name": "Test", "cron": "0 8 * * *",
            "sections": ["invalid_section"], "channels": ["ntfy"]
        })
        assert r.status_code == 422

    def test_create_job_rejects_topics_with_high_freq_cron(self):
        mgr = MagicMock()
        client = _make_app(mgr)
        r = client.post("/api/scheduler/jobs", json={
            "name": "Test", "cron": "*/15 * * * *",
            "sections": ["topics"], "channels": ["ntfy"]
        })
        assert r.status_code == 400

    def test_delete_system_job_returns_403(self):
        mgr = MagicMock()
        mgr.delete_job.side_effect = PermissionError("System jobs cannot be deleted")
        client = _make_app(mgr)
        r = client.delete("/api/scheduler/jobs/sys-1")
        assert r.status_code == 403

    def test_manual_run_returns_409_if_running(self):
        mgr = MagicMock()
        mgr.get_job.return_value = {"last_status": "running", "id": "job-1"}
        client = _make_app(mgr)
        r = client.post("/api/scheduler/jobs/job-1/run")
        assert r.status_code == 409

    def test_history_pagination(self):
        mgr = MagicMock()
        mgr.get_job_history.return_value = []
        client = _make_app(mgr)
        r = client.get("/api/scheduler/jobs/job-1/history?limit=10&offset=5")
        assert r.status_code == 200
        mgr.get_job_history.assert_called_once_with("job-1", limit=10, offset=5)
