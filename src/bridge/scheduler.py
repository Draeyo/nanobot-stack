"""Scheduler — APScheduler-based task runner for periodic briefings and custom jobs."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("rag-bridge.scheduler")

STATE_DIR = Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
VALID_SECTIONS = frozenset({"system_health", "personal_notes", "topics", "reminders", "weekly_summary", "custom"})
VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp"})


class SchedulerManager:
    """Manages scheduled jobs using APScheduler with SQLite persistence."""

    def __init__(self, broadcast_notifier: Any, qdrant_client: Any = None) -> None:
        self._notifier = broadcast_notifier
        self._qdrant = qdrant_client
        self._db_path = str(STATE_DIR / "scheduler.db")
        self._scheduler = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize APScheduler and restore jobs from DB. Called on app startup."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._reset_orphaned_jobs()

        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{STATE_DIR}/apscheduler.db")
        }
        self._scheduler = AsyncIOScheduler(jobstores=jobstores)
        self._scheduler.start()
        self._reschedule_all()
        logger.info("SchedulerManager started")

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("SchedulerManager stopped")

    def _reset_orphaned_jobs(self) -> None:
        """Reset jobs and runs stuck in 'running' state after a crash."""
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "UPDATE scheduled_jobs SET last_status='error', updated_at=? WHERE last_status='running'",
                (now,)
            )
            db.execute(
                "UPDATE job_runs SET status='error', error='interrupted by process restart' "
                "WHERE status='running'"
            )
            db.commit()
        finally:
            db.close()

    def _reschedule_all(self) -> None:
        """Re-add all enabled jobs to APScheduler after restart."""
        for job in self.list_jobs():
            if job["enabled"]:
                self._schedule_job(job)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def list_jobs(self) -> list[dict[str, Any]]:
        db = self._db()
        try:
            rows = db.execute(
                "SELECT id, name, cron, prompt, sections, channels, enabled, system, "
                "timeout_s, last_run, last_status, last_output, created_at, updated_at "
                "FROM scheduled_jobs ORDER BY created_at ASC"
            ).fetchall()
            keys = ["id", "name", "cron", "prompt", "sections", "channels", "enabled",
                    "system", "timeout_s", "last_run", "last_status", "last_output",
                    "created_at", "updated_at"]
            jobs = [dict(zip(keys, r)) for r in rows]
            for j in jobs:
                j["sections"] = json.loads(j["sections"] or "[]")
                j["channels"] = json.loads(j["channels"] or "[]")
                j["next_run_time"] = self._next_run_time(j["id"])
            return jobs
        finally:
            db.close()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        db = self._db()
        try:
            row = db.execute(
                "SELECT id, name, cron, prompt, sections, channels, enabled, system, "
                "timeout_s, last_run, last_status, last_output, created_at, updated_at "
                "FROM scheduled_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                return None
            keys = ["id", "name", "cron", "prompt", "sections", "channels", "enabled",
                    "system", "timeout_s", "last_run", "last_status", "last_output",
                    "created_at", "updated_at"]
            j = dict(zip(keys, row))
            j["sections"] = json.loads(j["sections"] or "[]")
            j["channels"] = json.loads(j["channels"] or "[]")
            j["next_run_time"] = self._next_run_time(job_id)
            return j
        finally:
            db.close()

    def create_job(self, name: str, cron: str, sections: list[str], channels: list[str],
                   prompt: str = "", timeout_s: int = 60, system: int = 0) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db = self._db()
        try:
            db.execute(
                "INSERT INTO scheduled_jobs (id, name, cron, prompt, sections, channels, "
                "enabled, system, timeout_s, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                (job_id, name, cron, prompt, json.dumps(sections), json.dumps(channels),
                 system, timeout_s, now, now)
            )
            db.commit()
        finally:
            db.close()
        self._schedule_job({"id": job_id, "cron": cron, "enabled": 1})
        return job_id

    def update_job(self, job_id: str, **fields) -> None:
        now = datetime.now(timezone.utc).isoformat()
        allowed = {"name", "cron", "prompt", "sections", "channels", "timeout_s", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "sections" in updates:
            updates["sections"] = json.dumps(updates["sections"])
        if "channels" in updates:
            updates["channels"] = json.dumps(updates["channels"])
        updates["updated_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [job_id]
        db = self._db()
        try:
            db.execute(f"UPDATE scheduled_jobs SET {set_clause} WHERE id=?", values)
            db.commit()
        finally:
            db.close()
        if "cron" in fields or "enabled" in fields:
            job = self.get_job(job_id)
            if job:
                self._reschedule_one(job)

    def delete_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        if job["system"]:
            raise PermissionError("System jobs cannot be deleted")
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        db = self._db()
        try:
            db.execute("DELETE FROM job_runs WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
            db.commit()
        finally:
            db.close()

    def toggle_job(self, job_id: str, enabled: bool) -> None:
        self.update_job(job_id, enabled=1 if enabled else 0)

    def get_job_history(self, job_id: str, limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
        db = self._db()
        try:
            rows = db.execute(
                "SELECT id, job_id, started_at, duration_ms, status, output, error, channels_ok "
                "FROM job_runs WHERE job_id=? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (job_id, limit, offset)
            ).fetchall()
            keys = ["id", "job_id", "started_at", "duration_ms", "status", "output", "error", "channels_ok"]
            return [dict(zip(keys, r)) for r in rows]
        finally:
            db.close()

    # ------------------------------------------------------------------
    # APScheduler helpers
    # ------------------------------------------------------------------

    def _schedule_job(self, job: dict) -> None:
        if not self._scheduler:
            return
        if not job.get("enabled", True):
            return
        try:
            self._scheduler.add_job(
                self._execute_job,
                trigger="cron",
                id=job["id"],
                args=[job["id"]],
                replace_existing=True,
                max_instances=1,
                **self._parse_cron(job["cron"]),
            )
        except Exception:
            logger.exception("Failed to schedule job %s", job["id"])

    def _reschedule_one(self, job: dict) -> None:
        if not self._scheduler:
            return
        try:
            self._scheduler.remove_job(job["id"])
        except Exception:
            pass
        if job.get("enabled"):
            self._schedule_job(job)

    def _next_run_time(self, job_id: str) -> str | None:
        if not self._scheduler:
            return None
        try:
            j = self._scheduler.get_job(job_id)
            if j and j.next_run_time:
                return j.next_run_time.isoformat()
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_cron(cron_expr: str) -> dict:
        """Convert '0 8 * * *' to APScheduler kwargs."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")
        minute, hour, day, month, day_of_week = parts
        return {
            "minute": minute, "hour": hour, "day": day,
            "month": month, "day_of_week": day_of_week,
        }

    # ------------------------------------------------------------------
    # Execution (stub — JobExecutor fills this in Task 5+6)
    # ------------------------------------------------------------------

    async def _execute_job(self, job_id: str) -> None:
        """Dispatched by APScheduler. Full implementation in JobExecutor."""
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=self._db_path, notifier=self._notifier, qdrant=self._qdrant)
        await executor.run(job_id)
