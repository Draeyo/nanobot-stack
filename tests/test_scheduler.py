"""Tests for SchedulerManager (CRUD, startup cleanup)."""
# pylint: disable=protected-access
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

    def test_get_job_returns_none_for_missing(self, tmp_db):
        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()
        mgr._scheduler.get_job.return_value = None
        assert mgr.get_job("nonexistent") is None

    def test_get_job_returns_job_with_parsed_sections(self, tmp_db):
        _insert_job(tmp_db, "job-1")
        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)
        mgr._scheduler = MagicMock()
        mgr._scheduler.get_job.return_value = None
        job = mgr.get_job("job-1")
        assert job is not None
        assert isinstance(job["sections"], list)
        assert isinstance(job["channels"], list)
        assert job["name"] == "Test Job"

    def test_get_job_history_pagination(self, tmp_db):
        _insert_job(tmp_db, "job-1")
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(str(tmp_db))
        for i in range(5):
            db.execute(
                "INSERT INTO job_runs (id, job_id, started_at, status) VALUES (?,?,?,?)",
                (f"run-{i}", "job-1", now, "ok")
            )
        db.commit()
        db.close()

        from scheduler import SchedulerManager
        mgr = SchedulerManager.__new__(SchedulerManager)
        mgr._db_path = str(tmp_db)

        all_runs = mgr.get_job_history("job-1", limit=5, offset=0)
        page = mgr.get_job_history("job-1", limit=2, offset=2)
        assert len(all_runs) == 5
        assert len(page) == 2


class TestSectionCollectors:
    async def test_resolve_template_variables(self):
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=":memory:", notifier=MagicMock(), qdrant=None)
        result = executor._resolve_template("Hello {{job_name}} on {{hostname}}", job_name="Test")
        assert "Test" in result
        assert "{{job_name}}" not in result
        assert "{{hostname}}" not in result

    async def test_personal_notes_window_24h_for_daily_cron(self):
        """Cron running once/day should use 24h window."""
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=":memory:", notifier=MagicMock(), qdrant=None)
        window = executor._notes_window_hours("0 8 * * *", last_run=None)
        assert window == 24

    async def test_personal_notes_window_since_last_run_for_sub_daily(self):
        """Cron running every 30 min should use last_run window."""
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=":memory:", notifier=MagicMock(), qdrant=None)
        window = executor._notes_window_hours("*/30 * * * *", last_run=None)
        assert window <= 2  # defaults to 1h when no last_run

    async def test_topics_section_blocked_for_high_frequency_cron(self):
        """topics section should not be collected for cron < 6h interval."""
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=":memory:", notifier=MagicMock(), qdrant=None)
        # Every 30 min = < 6h
        assert executor._is_high_frequency("*/30 * * * *") is True
        # Once daily = not high frequency
        assert executor._is_high_frequency("0 8 * * *") is False


class TestJobExecutorRun:
    async def test_run_marks_job_running_then_ok(self, tmp_db):
        from scheduler_executor import JobExecutor
        _insert_job(tmp_db, "job-1")

        notifier = MagicMock()
        notifier.broadcast = AsyncMock(return_value={"ntfy": True})

        with patch("scheduler_executor.JobExecutor._call_llm", new_callable=AsyncMock) as llm_mock:
            llm_mock.return_value = "Voici votre briefing."
            executor = JobExecutor(db_path=str(tmp_db), notifier=notifier, qdrant=None)
            await executor.run("job-1")

        db = sqlite3.connect(str(tmp_db))
        row = db.execute("SELECT last_status FROM scheduled_jobs WHERE id='job-1'").fetchone()
        run_row = db.execute("SELECT status FROM job_runs WHERE job_id='job-1'").fetchone()
        db.close()
        assert row[0] == "ok"
        assert run_row[0] == "ok"

    async def test_run_skips_if_already_running(self, tmp_db):
        """If last_status is 'running', skip execution silently."""
        _insert_job(tmp_db, "job-1", status="running")

        from scheduler_executor import JobExecutor
        notifier = MagicMock()
        notifier.broadcast = AsyncMock(return_value={})
        executor = JobExecutor(db_path=str(tmp_db), notifier=notifier, qdrant=None)

        with patch("scheduler_executor.JobExecutor._call_llm", new_callable=AsyncMock) as llm_mock:
            await executor.run("job-1")
            llm_mock.assert_not_called()  # skipped

    async def test_run_records_channels_ok(self, tmp_db):
        from scheduler_executor import JobExecutor
        _insert_job(tmp_db, "job-1")

        notifier = MagicMock()
        notifier.broadcast = AsyncMock(return_value={"ntfy": True, "telegram": False})

        with patch("scheduler_executor.JobExecutor._call_llm", new_callable=AsyncMock) as llm_mock:
            llm_mock.return_value = "Briefing content."
            executor = JobExecutor(db_path=str(tmp_db), notifier=notifier, qdrant=None)
            await executor.run("job-1")

        db = sqlite3.connect(str(tmp_db))
        run = db.execute("SELECT channels_ok FROM job_runs WHERE job_id='job-1'").fetchone()
        db.close()
        channels_ok = json.loads(run[0])
        assert channels_ok["ntfy"] is True
        assert channels_ok["telegram"] is False


class TestJobRegistry:
    def test_seeds_three_system_jobs(self, tmp_db):
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock._db_path = str(tmp_db)
        mgr_mock.list_jobs.return_value = []

        registry = JobRegistry(mgr_mock)
        registry.seed()

        assert mgr_mock.create_job.call_count == 3

    def test_does_not_seed_if_system_jobs_exist(self):
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock.list_jobs.return_value = [{"id": "sys-1", "system": 1}]

        registry = JobRegistry(mgr_mock)
        registry.seed()

        mgr_mock.create_job.assert_not_called()

    def test_seeds_even_if_custom_jobs_exist(self):
        """If only custom (non-system) jobs exist, system jobs should still be seeded."""
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock.list_jobs.return_value = [{"id": "custom-1", "system": 0}]

        registry = JobRegistry(mgr_mock)
        registry.seed()

        assert mgr_mock.create_job.call_count == 3
