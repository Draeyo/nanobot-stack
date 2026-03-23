"""Tests for SchedulerManager (CRUD, startup cleanup)."""
from __future__ import annotations
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


def _insert_job(db_path: Path, job_id: str, status: str = "ok", system: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO scheduled_jobs (id, name, cron, sections, channels, enabled, system, "
        "timeout_s, last_status, created_at, updated_at) VALUES (?,?,?,?,?,1,?,60,?,?,?)",
        (job_id, "Test Job", "0 8 * * *", '["system_health"]', '["ntfy"]', system, status, now, now)
    )
    db.commit()
    db.close()


class TestStartupCleanup:
    def test_orphaned_running_jobs_reset_on_startup(self, tmp_db):
        _insert_job(tmp_db, "job-1", status="running")
        _insert_job(tmp_db, "job-2", status="ok")

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._reset_orphaned_jobs()

        db = sqlite3.connect(str(tmp_db))
        rows = dict(db.execute("SELECT id, last_status FROM scheduled_jobs").fetchall())
        db.close()
        assert rows["job-1"] == "error"  # was running, now reset
        assert rows["job-2"] == "ok"     # untouched

    def test_orphaned_job_runs_reset_on_startup(self, tmp_db):
        _insert_job(tmp_db, "job-1", status="running")
        db = sqlite3.connect(str(tmp_db))
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO job_runs (id, job_id, started_at, status) VALUES (?,?,?,?)",
            ("run-1", "job-1", now, "running")
        )
        db.commit()
        db.close()

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._reset_orphaned_jobs()

        db = sqlite3.connect(str(tmp_db))
        run = db.execute("SELECT status, error FROM job_runs WHERE id='run-1'").fetchone()
        db.close()
        assert run[0] == "error"
        assert "restart" in run[1]


class TestJobCRUD:
    def test_create_job_persists_to_db(self, tmp_db):
        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()

        job_id = mgr.create_job(
            name="My Job",
            cron="0 8 * * *",
            sections=["system_health"],
            channels=["ntfy"],
            prompt="",
            timeout_s=60,
        )

        db = sqlite3.connect(str(tmp_db))
        row = db.execute("SELECT name, cron FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
        db.close()
        assert row == ("My Job", "0 8 * * *")

    def test_delete_system_job_raises(self, tmp_db):
        _insert_job(tmp_db, "sys-1", system=1)

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()

        with pytest.raises(PermissionError):
            mgr.delete_job("sys-1")

    def test_toggle_job(self, tmp_db):
        _insert_job(tmp_db, "job-1")

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()

        mgr.toggle_job("job-1", enabled=False)

        db = sqlite3.connect(str(tmp_db))
        enabled = db.execute("SELECT enabled FROM scheduled_jobs WHERE id='job-1'").fetchone()[0]
        db.close()
        assert enabled == 0

    def test_list_jobs_returns_all(self, tmp_db):
        _insert_job(tmp_db, "job-1")
        _insert_job(tmp_db, "job-2")

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()
        mgr._scheduler.get_job.return_value = None  # no next_run_time

        jobs = mgr.list_jobs()
        assert len(jobs) == 2
