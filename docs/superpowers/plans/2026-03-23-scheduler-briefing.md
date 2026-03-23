# Scheduler & Briefing Matinal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full scheduled-task system with configurable briefings delivered to all channels, managed via a new Admin UI tab.

**Architecture:** `APScheduler (AsyncIOScheduler)` + `SQLAlchemyJobStore` runs in-process inside the FastAPI bridge, initialized via `@app.on_event("startup")`. A `BroadcastNotifier` fans out messages to ntfy/Telegram/Discord/WhatsApp. The entire feature is self-contained across 3 new modules (`scheduler.py`, `broadcast_notifier.py`, `scheduler_api.py`) plus targeted additions to `app.py` and `admin_ui.py`.

**Tech Stack:** Python 3.10+, FastAPI, APScheduler 3.x, SQLAlchemy 2.x (for SQLAlchemyJobStore), croniter, litellm, pytest + unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-23-scheduler-briefing-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `migrations/011_scheduler.py` | Create | Schema: `scheduled_jobs` + `job_runs` tables |
| `src/bridge/broadcast_notifier.py` | Create | Fan-out messages to ntfy/Telegram/Discord/WhatsApp |
| `src/bridge/scheduler.py` | Create | `SchedulerManager`, `JobExecutor`, `JobRegistry` |
| `src/bridge/scheduler_executor.py` | Create | `JobExecutor` — section collectors + LLM pipeline |
| `src/bridge/scheduler_api.py` | Create | REST endpoints `/api/scheduler/*` |
| `src/bridge/app.py` | Modify | Wire scheduler startup/shutdown + include router |
| `src/bridge/admin_ui.py` | Modify | Add "Scheduler" tab (11th tab) |
| `src/bridge/requirements.txt` | Modify | Add `apscheduler>=3.10`, `croniter>=2.0`, `sqlalchemy>=2.0` |
| `src/config/model_router.json` | Modify | Add `"briefing"` task route |
| `tests/conftest.py` | Create | Shared pytest fixtures |
| `tests/test_broadcast_notifier.py` | Create | Unit tests for BroadcastNotifier |
| `tests/test_scheduler.py` | Create | Unit tests for SchedulerManager + JobExecutor |
| `tests/test_scheduler_api.py` | Create | Integration tests for REST endpoints |

---

## Task 1: Dependencies + Model Router + Migration

**Files:**
- Modify: `src/bridge/requirements.txt`
- Modify: `src/config/model_router.json`
- Create: `migrations/011_scheduler.py`

- [ ] **Step 1: Verify next migration number**

```bash
ls migrations/
# Expected: 008_initial.py  010_v10_evolution.py  run_migrations.py
# 011 is the next available number (009 is a gap but version is already 10, so use 011)
```

- [ ] **Step 2: Add dependencies to requirements.txt**

In `src/bridge/requirements.txt`, append:
```
apscheduler>=3.10
croniter>=2.0
sqlalchemy>=2.0
```

- [ ] **Step 3: Add briefing task type to model_router.json**

In `src/config/model_router.json`, inside the `"task_routes"` object, add after `"classify_query"`:
```json
"briefing": ["router_fast", "classifier_backup", "local_fast"],
```

- [ ] **Step 4: Create migration file**

Create `migrations/011_scheduler.py`:
```python
"""011_scheduler — scheduled_jobs and job_runs tables."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 11

logger = logging.getLogger("migration.v11")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                cron        TEXT NOT NULL,
                prompt      TEXT NOT NULL DEFAULT '',
                sections    TEXT NOT NULL DEFAULT '[]',
                channels    TEXT NOT NULL DEFAULT '[]',
                enabled     INTEGER NOT NULL DEFAULT 1,
                system      INTEGER NOT NULL DEFAULT 0,
                timeout_s   INTEGER NOT NULL DEFAULT 60,
                last_run    TEXT,
                last_status TEXT,
                last_output TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id          TEXT PRIMARY KEY,
                job_id      TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                duration_ms INTEGER,
                status      TEXT NOT NULL,
                output      TEXT,
                error       TEXT,
                channels_ok TEXT
            )
        """)
        db.commit()
        logger.info("Migration 011: scheduler tables created at %s", db_path)
    finally:
        db.close()
```

- [ ] **Step 5: Commit**

```bash
git add migrations/011_scheduler.py src/bridge/requirements.txt src/config/model_router.json
git commit -m "feat(scheduler): add migration 011, dependencies, and briefing task route"
```

---

## Task 2: Test Infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`

- [ ] **Step 1: Create test directory structure**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Create pytest.ini at repo root**

Create `/c/Users/draey/Downloads/nanobot-stack-repo/pytest.ini`:
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

- [ ] **Step 3: Create conftest.py**

Create `tests/conftest.py`:
```python
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
```

- [ ] **Step 4: Verify pytest is importable**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo
pip install pytest pytest-asyncio --quiet
pytest --collect-only 2>&1 | head -5
# Expected: "no tests ran" or "0 items" — that's fine, infrastructure is ready
```

- [ ] **Step 5: Commit**

```bash
git add pytest.ini tests/
git commit -m "test: add pytest infrastructure and shared fixtures"
```

---

## Task 3: BroadcastNotifier (TDD)

**Files:**
- Create: `tests/test_broadcast_notifier.py`
- Create: `src/bridge/broadcast_notifier.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_broadcast_notifier.py`:
```python
"""Tests for BroadcastNotifier."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


class TestBroadcastNotifierNtfy:
    async def test_ntfy_channel_calls_send_notification(self, mock_send_notification):
        from broadcast_notifier import BroadcastNotifier
        notifier = BroadcastNotifier(channel_manager=MagicMock())
        result = await notifier.broadcast(["ntfy"], "Hello world")
        mock_send_notification.assert_called_once_with("Hello world")
        assert result["ntfy"] is True

    async def test_ntfy_failure_recorded(self):
        from broadcast_notifier import BroadcastNotifier
        with patch("tools.send_notification", new_callable=AsyncMock) as m:
            m.return_value = {"ok": False, "error": "timeout"}
            notifier = BroadcastNotifier(channel_manager=MagicMock())
            result = await notifier.broadcast(["ntfy"], "Hello")
            assert result["ntfy"] is False


class TestBroadcastNotifierChannels:
    async def test_telegram_fans_out_to_approved_users(self, mock_dm_pairing, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        telegram_adapter = AsyncMock()
        telegram_adapter.send_message = AsyncMock(return_value={"ok": True})
        mock_channel_manager.get_adapter.return_value = telegram_adapter

        notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
        result = await notifier.broadcast(["telegram"], "Briefing!")

        # Only telegram user (platform_id "telegram:111") should receive message
        telegram_adapter.send_message.assert_called_once_with("111", "Briefing!")
        assert result["telegram"] is True

    async def test_one_channel_failure_does_not_block_others(self, mock_dm_pairing, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        failing_adapter = AsyncMock()
        failing_adapter.send_message = AsyncMock(side_effect=Exception("network error"))
        mock_channel_manager.get_adapter.return_value = failing_adapter

        with patch("tools.send_notification", new_callable=AsyncMock) as ntfy_mock:
            ntfy_mock.return_value = {"ok": True}
            notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
            result = await notifier.broadcast(["ntfy", "telegram"], "Test")

        assert result["ntfy"] is True
        assert result["telegram"] is False  # failed, but ntfy succeeded

    async def test_no_approved_users_returns_true(self, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        with patch("dm_pairing.list_approved_users", return_value=[]):
            notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
            result = await notifier.broadcast(["telegram"], "Hello")
        assert result["telegram"] is True  # no users = nothing to fail
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo
pytest tests/test_broadcast_notifier.py -v 2>&1 | head -20
# Expected: ImportError or ModuleNotFoundError for broadcast_notifier
```

- [ ] **Step 3: Implement BroadcastNotifier**

Create `src/bridge/broadcast_notifier.py`:
```python
"""BroadcastNotifier — fan-out messages to all configured delivery channels."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("rag-bridge.broadcast_notifier")

VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp"})


class BroadcastNotifier:
    """Delivers a message to one or more channels in parallel.

    Args:
        channel_manager: The ChannelManager instance from channels/__init__.py.
    """

    def __init__(self, channel_manager: Any) -> None:
        self._channel_manager = channel_manager

    async def broadcast(self, channels: list[str], message: str) -> dict[str, bool]:
        """Send message to each channel. Returns {channel: success}."""
        import asyncio
        tasks = {ch: self._deliver(ch, message) for ch in channels if ch in VALID_CHANNELS}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {ch: (not isinstance(r, Exception) and r) for ch, r in zip(tasks.keys(), results)}

    async def _deliver(self, channel: str, message: str) -> bool:
        if channel == "ntfy":
            return await self._deliver_ntfy(message)
        return await self._deliver_adapter(channel, message)

    async def _deliver_ntfy(self, message: str) -> bool:
        try:
            from tools import send_notification
            result = await send_notification(message)
            return bool(result.get("ok"))
        except Exception:
            logger.exception("ntfy delivery failed")
            return False

    async def _deliver_adapter(self, platform: str, message: str) -> bool:
        try:
            from dm_pairing import list_approved_users
            users = [
                u for u in list_approved_users()
                if u["platform_id"].split(":")[0] == platform
            ]
            if not users:
                return True  # no users to notify = not a failure

            adapter = self._channel_manager.get_adapter(platform)
            if adapter is None:
                logger.warning("No adapter registered for channel: %s", platform)
                return False

            for user in users:
                numeric_id = user["platform_id"].split(":", 1)[1]
                try:
                    await adapter.send_message(numeric_id, message)
                except Exception:
                    logger.exception("Failed to deliver to %s via %s", user["platform_id"], platform)
            return True
        except Exception:
            logger.exception("%s delivery failed", platform)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_broadcast_notifier.py -v
# Expected: all 5 tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/bridge/broadcast_notifier.py tests/test_broadcast_notifier.py
git commit -m "feat(scheduler): add BroadcastNotifier with multi-channel fan-out"
```

---

## Task 4: SchedulerManager Core (TDD)

**Files:**
- Create: `tests/test_scheduler.py` (CRUD + startup cleanup)
- Create: `src/bridge/scheduler.py` (SchedulerManager only, no JobExecutor yet)

- [ ] **Step 1: Write failing tests for SchedulerManager**

Create `tests/test_scheduler.py`:
```python
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_scheduler.py -v 2>&1 | head -20
# Expected: ImportError for scheduler
```

- [ ] **Step 3: Implement SchedulerManager**

Create `src/bridge/scheduler.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scheduler.py -v
# Expected: all 6 tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/bridge/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): add SchedulerManager with CRUD and startup cleanup"
```

---

## Task 5: JobExecutor — Section Collectors (TDD)

**Files:**
- Create: `src/bridge/scheduler_executor.py`
- Add tests to: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests for section collectors**

Append to `tests/test_scheduler.py`:
```python
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
        from croniter import croniter
        executor = JobExecutor(db_path=":memory:", notifier=MagicMock(), qdrant=None)
        # Every 30 min = < 6h
        assert executor._is_high_frequency("*/30 * * * *") is True
        # Once daily = not high frequency
        assert executor._is_high_frequency("0 8 * * *") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_scheduler.py::TestSectionCollectors -v 2>&1 | head -15
# Expected: ImportError for scheduler_executor
```

- [ ] **Step 3: Implement scheduler_executor.py (section collectors)**

Create `src/bridge/scheduler_executor.py`:
```python
"""JobExecutor — collects sections, calls LLM, delivers via BroadcastNotifier."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("rag-bridge.scheduler_executor")

SECTION_LABELS = {
    "system_health": "Santé système",
    "personal_notes": "Notes récentes",
    "topics": "Sujets d'intérêt",
    "reminders": "Rappels",
    "weekly_summary": "Bilan de la semaine",
    "custom": "Note personnalisée",
}


class JobExecutor:
    """Executes a scheduled job: collect sections → LLM → deliver."""

    def __init__(self, db_path: str, notifier: Any, qdrant: Any = None) -> None:
        self._db_path = db_path
        self._notifier = notifier
        self._qdrant = qdrant

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------

    def _resolve_template(self, template: str, job_name: str = "") -> str:
        now = datetime.now(timezone.utc)
        return (
            template
            .replace("{{date}}", now.strftime("%A %d %B %Y"))
            .replace("{{time}}", now.strftime("%H:%M"))
            .replace("{{day}}", now.strftime("%A"))
            .replace("{{hostname}}", socket.gethostname())
            .replace("{{job_name}}", job_name)
            .replace("{{last_run}}", "N/A")  # overridden in run()
        )

    def _notes_window_hours(self, cron: str, last_run: str | None) -> int:
        """Return the time window in hours for personal_notes queries."""
        interval_minutes = self._cron_interval_minutes(cron)
        if interval_minutes < 24 * 60:
            # sub-daily: use 1h if no last_run, else since last_run
            if last_run:
                try:
                    lr = datetime.fromisoformat(last_run)
                    delta = (datetime.now(timezone.utc) - lr).total_seconds() / 3600
                    return max(1, int(delta) + 1)
                except Exception:
                    pass
            return 1
        return 24

    def _is_high_frequency(self, cron: str) -> bool:
        """True if cron fires more often than every 6 hours."""
        return self._cron_interval_minutes(cron) < 6 * 60

    @staticmethod
    def _cron_interval_minutes(cron: str) -> int:
        """Estimate minimum interval in minutes for a cron expression."""
        try:
            from croniter import croniter
            c = croniter(cron)
            t1 = c.get_next(float)
            t2 = c.get_next(float)
            return max(1, int((t2 - t1) / 60))
        except Exception:
            return 1440  # assume daily on error

    # ------------------------------------------------------------------
    # Section collectors
    # ------------------------------------------------------------------

    async def _collect_system_health(self) -> str:
        try:
            import subprocess
            lines = []
            for cmd in [
                ["df", "-h", "/"],
                ["free", "-h"],
                ["uptime"],
            ]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    lines.append(r.stdout.strip())
                except Exception:
                    pass
            return "\n".join(lines) or "System info unavailable"
        except Exception as e:
            return f"system_health error: {e}"

    async def _collect_personal_notes(self, window_hours: int) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
            results = self._qdrant.scroll(
                collection_name="personal_memories",
                scroll_filter={"must": [{"key": "created_at", "range": {"gte": since}}]},
                limit=20,
            )
            points = results[0] if results else []
            if not points:
                return "Aucune nouvelle note."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"personal_notes error: {e}"

    async def _collect_reminders(self) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            results = self._qdrant.scroll(
                collection_name="personal_memories",
                scroll_filter={"must": [{"key": "tags", "match": {"any": ["reminder"]}}]},
                limit=20,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun rappel actif."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"reminders error: {e}"

    async def _collect_weekly_summary(self) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            results = self._qdrant.scroll(
                collection_name="conversation_summaries",
                scroll_filter={"must": [{"key": "created_at", "range": {"gte": since}}]},
                limit=30,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun résumé cette semaine."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"weekly_summary error: {e}"

    async def _collect_topics(self) -> str:
        """Queries Qdrant documents and summarizes via LLM. Expensive — avoid for high-freq jobs."""
        if not self._qdrant:
            return "Qdrant not available"
        try:
            results = self._qdrant.scroll(
                collection_name="documents",
                limit=10,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun document indexé."
            snippets = "\n".join(f"- {p.payload.get('content', '')[:200]}" for p in points[:5])
            return snippets
        except Exception as e:
            return f"topics error: {e}"

    async def collect_sections(self, sections: list[str], cron: str,
                                last_run: str | None, prompt: str, job_name: str) -> str:
        """Collect all enabled sections in parallel and assemble the prompt."""
        tasks: dict[str, Any] = {}
        window_h = self._notes_window_hours(cron, last_run)

        for sec in sections:
            if sec == "system_health":
                tasks[sec] = self._collect_system_health()
            elif sec == "personal_notes":
                tasks[sec] = self._collect_personal_notes(window_h)
            elif sec == "reminders":
                tasks[sec] = self._collect_reminders()
            elif sec == "weekly_summary":
                tasks[sec] = self._collect_weekly_summary()
            elif sec == "topics":
                tasks[sec] = self._collect_topics()

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        section_data = dict(zip(tasks.keys(), results))

        parts = []
        for sec, data in section_data.items():
            label = SECTION_LABELS.get(sec, sec)
            content = str(data) if not isinstance(data, Exception) else f"Erreur: {data}"
            parts.append(f"## {label}\n{content}")

        if "custom" in sections and prompt:
            resolved = self._resolve_template(prompt, job_name=job_name)
            parts.append(f"## Note\n{resolved}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Main execution loop (LLM + delivery — implemented in Task 6)
    # ------------------------------------------------------------------

    async def run(self, job_id: str) -> None:
        """Full job execution. Called by SchedulerManager._execute_job()."""
        # Implemented in Task 6
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scheduler.py::TestSectionCollectors -v
# Expected: all 4 tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/bridge/scheduler_executor.py tests/test_scheduler.py
git commit -m "feat(scheduler): add JobExecutor section collectors"
```

---

## Task 6: JobExecutor — LLM Pipeline + Full Execution (TDD)

**Files:**
- Add tests to: `tests/test_scheduler.py`
- Modify: `src/bridge/scheduler_executor.py` (implement `run()`)

- [ ] **Step 1: Write failing tests for full execution pipeline**

Append to `tests/test_scheduler.py`:
```python
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

    async def test_run_409_if_already_running(self, tmp_db):
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_scheduler.py::TestJobExecutorRun -v 2>&1 | head -15
# Expected: NotImplementedError (run() not yet implemented)
```

- [ ] **Step 3: Implement run() in scheduler_executor.py**

Replace the `run()` stub in `src/bridge/scheduler_executor.py` with:
```python
    async def run(self, job_id: str) -> None:
        """Full job execution: collect → LLM → deliver → persist."""
        db = sqlite3.connect(self._db_path)
        try:
            row = db.execute(
                "SELECT name, cron, prompt, sections, channels, timeout_s, last_run, last_status "
                "FROM scheduled_jobs WHERE id=?", (job_id,)
            ).fetchone()
        finally:
            db.close()

        if not row:
            logger.warning("Job %s not found", job_id)
            return

        name, cron, prompt, sections_json, channels_json, timeout_s, last_run, last_status = row

        # Skip if already running (APScheduler max_instances=1 guards concurrent scheduler
        # calls, but manual /run endpoint could race)
        if last_status == "running":
            logger.info("Job %s already running, skipping", job_id)
            return

        sections = json.loads(sections_json or "[]")
        channels = json.loads(channels_json or "[]")
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        # Mark as running
        self._update_job_status(job_id, "running", None, None)
        self._insert_run(run_id, job_id, started_at, "running", None, None, None)

        output = None
        error = None
        channels_ok: dict[str, bool] = {}
        try:
            # 1. Collect sections
            sections_text = await asyncio.wait_for(
                self.collect_sections(sections, cron, last_run, prompt, name),
                timeout=float(timeout_s)
            )

            # 2. Call LLM
            output = await asyncio.wait_for(
                self._call_llm(sections_text, name),
                timeout=float(timeout_s)
            )

            # 3. Deliver
            channels_ok = await self._notifier.broadcast(channels, output)

            # 4. Store in Qdrant
            if self._qdrant and output:
                try:
                    from qdrant_client.models import PointStruct
                    self._qdrant.upsert(
                        collection_name="conversation_summaries",
                        points=[PointStruct(
                            id=str(uuid.uuid4()),
                            vector=[0.0],  # placeholder; real embedding done by ingest pipeline
                            payload={"content": output[:500], "source": "scheduler", "job_id": job_id,
                                     "created_at": started_at}
                        )]
                    )
                except Exception:
                    logger.exception("Failed to store briefing in Qdrant")

            status = "ok"
        except asyncio.TimeoutError:
            status = "timeout"
            error = f"Job exceeded timeout of {timeout_s}s"
        except Exception as e:
            status = "error"
            error = str(e)
            logger.exception("Job %s failed", job_id)

        # Calculate duration
        finished_at = datetime.now(timezone.utc)
        duration_ms = int((finished_at - datetime.fromisoformat(started_at)).total_seconds() * 1000)
        output_preview = (output or "")[:500]

        self._update_job_status(job_id, status, started_at, output_preview)
        self._finalize_run(run_id, status, duration_ms,
                           (output or "")[:2000], error, json.dumps(channels_ok))

    async def _call_llm(self, context: str, job_name: str) -> str:
        """Call LLM via AdaptiveRouter for briefing generation."""
        try:
            import litellm
            from adaptive_router import AdaptiveRouter
            from model_config import get_model_config  # noqa: F401  — fallback if absent
        except ImportError:
            pass

        try:
            from adaptive_router import AdaptiveRouter
            import os, json as _json
            config_path = os.path.join(os.path.dirname(__file__),
                                       "..", "config", "model_router.json")
            with open(config_path) as f:
                router_cfg = _json.load(f)

            task_routes = router_cfg.get("task_routes", {})
            candidates_keys = task_routes.get("briefing", task_routes.get("classify_query", []))
            profiles = router_cfg.get("profiles", {})

            # Build model IDs from profile names
            candidate_models = []
            for key in candidates_keys:
                p = profiles.get(key, {})
                model = p.get("model")
                if model:
                    candidate_models.append(model)

            ar = AdaptiveRouter()
            ranked = ar.get_model_ranking("briefing", candidate_models)
            if not ranked:
                ranked = candidate_models

            messages = [
                {"role": "system", "content": "Tu es un assistant personnel. Génère un briefing clair et structuré en Markdown à partir des données fournies. Sois concis."},
                {"role": "user", "content": f"Briefing pour '{job_name}':\n\n{context}"},
            ]

            for model in ranked:
                try:
                    import litellm
                    resp = await litellm.acompletion(model=model, messages=messages, max_tokens=800)
                    result = resp.choices[0].message.content or ""
                    ar.record_quality("briefing", model, 0.8)
                    return result
                except Exception:
                    logger.warning("Model %s failed for briefing, trying next", model)

            return f"Briefing indisponible — tous les modèles ont échoué."
        except Exception as e:
            logger.exception("LLM call failed for briefing")
            return f"Briefing indisponible: {e}"

    def _update_job_status(self, job_id: str, status: str, last_run: str | None,
                            last_output: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "UPDATE scheduled_jobs SET last_status=?, last_run=COALESCE(?,last_run), "
                "last_output=COALESCE(?,last_output), updated_at=? WHERE id=?",
                (status, last_run, last_output, now, job_id)
            )
            db.commit()
        finally:
            db.close()

    def _insert_run(self, run_id: str, job_id: str, started_at: str,
                     status: str, output: str | None, error: str | None,
                     channels_ok: str | None) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "INSERT INTO job_runs (id, job_id, started_at, status, output, error, channels_ok) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, job_id, started_at, status, output, error, channels_ok)
            )
            db.commit()
        finally:
            db.close()

    def _finalize_run(self, run_id: str, status: str, duration_ms: int,
                       output: str, error: str | None, channels_ok: str) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "UPDATE job_runs SET status=?, duration_ms=?, output=?, error=?, channels_ok=? WHERE id=?",
                (status, duration_ms, output, error, channels_ok, run_id)
            )
            db.commit()
        finally:
            db.close()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scheduler.py -v
# Expected: all tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/bridge/scheduler_executor.py tests/test_scheduler.py
git commit -m "feat(scheduler): implement JobExecutor.run() with LLM pipeline and delivery"
```

---

## Task 7: JobRegistry — System Jobs (TDD)

**Files:**
- Create: `src/bridge/scheduler_registry.py`
- Add tests to: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_scheduler.py`:
```python
class TestJobRegistry:
    def test_seeds_three_system_jobs(self, tmp_db):
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock._db_path = str(tmp_db)
        mgr_mock.list_jobs.return_value = []

        registry = JobRegistry(mgr_mock)
        registry.seed()

        assert mgr_mock.create_job.call_count == 3

    def test_does_not_seed_if_system_jobs_exist(self, tmp_db):
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock.list_jobs.return_value = [{"id": "sys-1", "system": 1}]

        registry = JobRegistry(mgr_mock)
        registry.seed()

        mgr_mock.create_job.assert_not_called()

    def test_seeds_even_if_custom_jobs_exist(self, tmp_db):
        """If only custom (non-system) jobs exist, system jobs should still be seeded."""
        from scheduler_registry import JobRegistry
        mgr_mock = MagicMock()
        mgr_mock.list_jobs.return_value = [{"id": "custom-1", "system": 0}]

        registry = JobRegistry(mgr_mock)
        registry.seed()

        assert mgr_mock.create_job.call_count == 3
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_scheduler.py::TestJobRegistry -v 2>&1 | head -10
```

- [ ] **Step 3: Implement JobRegistry**

Create `src/bridge/scheduler_registry.py`:
```python
"""JobRegistry — seeds system jobs on first startup."""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("rag-bridge.scheduler_registry")

SYSTEM_JOBS = [
    {
        "name": "Briefing matinal",
        "cron": "0 8 * * *",
        "sections": ["system_health", "personal_notes", "reminders"],
        "channels": ["ntfy", "telegram", "discord", "whatsapp"],
        "prompt": "Bonjour ! Voici ton briefing du {{day}} {{date}}.",
        "timeout_s": 120,
    },
    {
        "name": "Surveillance système",
        "cron": "*/30 * * * *",
        "sections": ["system_health"],
        "channels": ["ntfy"],
        "prompt": "",
        "timeout_s": 30,
    },
    {
        "name": "Bilan hebdomadaire",
        "cron": "0 9 * * 1",
        "sections": ["weekly_summary"],
        "channels": ["ntfy", "telegram", "discord", "whatsapp"],
        "prompt": "Voici le bilan de la semaine.",
        "timeout_s": 120,
    },
]


class JobRegistry:
    def __init__(self, manager: Any) -> None:
        self._mgr = manager

    def seed(self) -> None:
        """Create system jobs if none exist yet. Idempotent: only seeds if no system jobs present."""
        if any(j.get("system") for j in self._mgr.list_jobs()):
            return
        for job in SYSTEM_JOBS:
            self._mgr.create_job(
                name=job["name"],
                cron=job["cron"],
                sections=job["sections"],
                channels=job["channels"],
                prompt=job["prompt"],
                timeout_s=job["timeout_s"],
                system=1,
            )
        logger.info("Seeded %d system jobs", len(SYSTEM_JOBS))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scheduler.py::TestJobRegistry -v
# Expected: 2 tests PASS
```

- [ ] **Step 5: Commit**

```bash
git add src/bridge/scheduler_registry.py tests/test_scheduler.py
git commit -m "feat(scheduler): add JobRegistry with 3 system jobs"
```

---

## Task 8: scheduler_api.py — REST Endpoints (TDD)

**Files:**
- Create: `tests/test_scheduler_api.py`
- Create: `src/bridge/scheduler_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_scheduler_api.py`:
```python
"""Tests for scheduler REST API."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from fastapi.testclient import TestClient
from fastapi import FastAPI


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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_scheduler_api.py -v 2>&1 | head -20
```

- [ ] **Step 3: Implement scheduler_api.py**

Create `src/bridge/scheduler_api.py`:
```python
"""REST API for scheduler management — mounted at /api/scheduler."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator

logger = logging.getLogger("rag-bridge.scheduler_api")

router = APIRouter(prefix="/api/scheduler")

_manager: Any = None
_verify_token = None

VALID_SECTIONS = frozenset({"system_health", "personal_notes", "topics", "reminders", "weekly_summary", "custom"})
VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp"})


def init_scheduler_api(manager: Any, verify_token_dep: Any) -> None:
    global _manager, _verify_token
    _manager = manager
    _verify_token = verify_token_dep


def _auth():
    return [Depends(_verify_token)] if _verify_token else []


def _validate_cron(cron: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(cron):
        raise HTTPException(422, detail=f"Invalid cron expression: '{cron}'")


def _validate_sections(sections: list[str]) -> None:
    invalid = [s for s in sections if s not in VALID_SECTIONS]
    if invalid:
        raise HTTPException(422, detail=f"Invalid sections: {invalid}. Valid: {sorted(VALID_SECTIONS)}")


def _validate_channels(channels: list[str]) -> None:
    if not channels:
        raise HTTPException(422, detail="At least one channel is required")
    invalid = [c for c in channels if c not in VALID_CHANNELS]
    if invalid:
        raise HTTPException(422, detail=f"Invalid channels: {invalid}. Valid: {sorted(VALID_CHANNELS)}")


def _validate_topics_frequency(sections: list[str], cron: str) -> None:
    if "topics" not in sections:
        return
    try:
        from scheduler_executor import JobExecutor
        if JobExecutor._cron_interval_minutes(cron) < 6 * 60:
            raise HTTPException(400, detail="Section 'topics' cannot be used with cron intervals < 6h (LLM cost risk)")
    except HTTPException:
        raise
    except Exception:
        pass


class JobCreate(BaseModel):
    name: str
    cron: str
    sections: list[str]
    channels: list[str]
    prompt: str = ""
    timeout_s: int = 60

    @field_validator("timeout_s")
    @classmethod
    def timeout_range(cls, v: int) -> int:
        if not (10 <= v <= 300):
            raise ValueError("timeout_s must be between 10 and 300")
        return v


class JobUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    sections: list[str] | None = None
    channels: list[str] | None = None
    prompt: str | None = None
    timeout_s: int | None = None
    enabled: bool | None = None


@router.get("/jobs")
def list_jobs():
    return _manager.list_jobs()


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate):
    _validate_cron(body.cron)
    _validate_sections(body.sections)
    _validate_channels(body.channels)
    _validate_topics_frequency(body.sections, body.cron)
    job_id = _manager.create_job(
        name=body.name, cron=body.cron, sections=body.sections,
        channels=body.channels, prompt=body.prompt, timeout_s=body.timeout_s,
    )
    return _manager.get_job(job_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job


@router.put("/jobs/{job_id}")
def update_job(job_id: str, body: JobUpdate):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    updates = body.model_dump(exclude_none=True)
    if "cron" in updates:
        _validate_cron(updates["cron"])
    if "sections" in updates:
        _validate_sections(updates["sections"])
    if "channels" in updates:
        _validate_channels(updates["channels"])
    cron = updates.get("cron", job["cron"])
    sections = updates.get("sections", job["sections"])
    _validate_topics_frequency(sections, cron)
    _manager.update_job(job_id, **updates)
    return _manager.get_job(job_id)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        _manager.delete_job(job_id)
    except PermissionError as e:
        raise HTTPException(403, detail=str(e))


@router.post("/jobs/{job_id}/run")
def run_job_now(job_id: str, background_tasks: BackgroundTasks):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.get("last_status") == "running":
        raise HTTPException(409, detail="Job is already running")
    background_tasks.add_task(_manager._execute_job, job_id)
    return {"queued": True, "job_id": job_id}


@router.post("/jobs/{job_id}/toggle")
def toggle_job(job_id: str, enabled: bool):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    _manager.toggle_job(job_id, enabled)
    return _manager.get_job(job_id)


@router.get("/jobs/{job_id}/history")
def job_history(job_id: str, limit: int = 30, offset: int = 0):
    return _manager.get_job_history(job_id, limit=min(limit, 100), offset=offset)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_scheduler_api.py -v
# Expected: all 7 tests PASS
```

- [ ] **Step 5: Run all tests to check for regressions**

```bash
pytest tests/ -v
# Expected: all tests PASS
```

- [ ] **Step 6: Commit**

```bash
git add src/bridge/scheduler_api.py tests/test_scheduler_api.py
git commit -m "feat(scheduler): add REST API with validation and pagination"
```

---

## Task 9: App.py Integration

**Files:**
- Modify: `src/bridge/app.py`

- [ ] **Step 1: Locate insertion points in app.py**

```bash
grep -n "on_event\|include_router\|channel_manager\|init_pairing" /c/Users/draey/Downloads/nanobot-stack-repo/src/bridge/app.py | tail -20
```

- [ ] **Step 2: Add imports at top of app.py**

In `src/bridge/app.py`, find the block of imports near the top (around line 20-30). Add after the existing imports:
```python
# Scheduler
from broadcast_notifier import BroadcastNotifier
from scheduler import SchedulerManager
from scheduler_api import router as scheduler_router, init_scheduler_api
from scheduler_registry import JobRegistry
```

- [ ] **Step 3: Initialize SchedulerManager OUTSIDE the channels try/except block**

The entire channel adapter block (lines ~1269–1310) is wrapped in a `try/except Exception` that silently swallows failures. The scheduler must start independently — place it **after** that entire block, not inside it.

Find the end of the channels block (after `_stop_channels` event handler) and add:
```python
# Scheduler — initialized outside channels try/except to start independently
_broadcast_notifier = BroadcastNotifier(channel_manager=channel_manager)
scheduler_manager = SchedulerManager(
    broadcast_notifier=_broadcast_notifier,
    qdrant_client=qdrant,  # the variable is named `qdrant` in app.py (line ~117)
)
init_scheduler_api(manager=scheduler_manager, verify_token_dep=verify_token)
app.include_router(scheduler_router)
```

> **Note:** The qdrant variable is named `qdrant` in app.py (not `qdrant_client`). Confirm with `grep -n "^qdrant" src/bridge/app.py`.

- [ ] **Step 4: Add startup/shutdown event handlers**

Near the `_start_channels` / `_stop_channels` event handlers (around line 1298), add:
```python
@app.on_event("startup")
async def _start_scheduler():
    scheduler_manager.start()
    JobRegistry(scheduler_manager).seed()
    logger.info("Scheduler started")

@app.on_event("shutdown")
async def _stop_scheduler():
    scheduler_manager.stop()
```

- [ ] **Step 5: Smoke test the API starts without errors**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo/src/bridge
pip install -r requirements.txt --quiet
python -c "from scheduler import SchedulerManager; print('OK')"
python -c "from scheduler_api import router; print('OK')"
python -c "from broadcast_notifier import BroadcastNotifier; print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/bridge/app.py
git commit -m "feat(scheduler): wire SchedulerManager into app.py startup/shutdown"
```

---

## Task 10: Admin UI — Scheduler Tab

**Files:**
- Modify: `src/bridge/admin_ui.py`

This task adds the 11th tab to the Admin UI. The UI is entirely embedded HTML/JS/CSS in Python string constants — no build step.

- [ ] **Step 1: Find tab insertion point in admin_ui.py**

```bash
grep -n "tab.*channels\|tab.*elevated\|ADMIN_TABS\|topnav" /c/Users/draey/Downloads/nanobot-stack-repo/src/bridge/admin_ui.py | head -20
```

- [ ] **Step 2: Add "Scheduler" tab to the navigation**

Find the `<a class="tab"` block in the topnav HTML and add a Scheduler tab at the end:
```html
<a class="tab" :class="{active:tab==='scheduler'}" @click="tab='scheduler'" href="#">Scheduler</a>
```

- [ ] **Step 3: Add scheduler section to main content**

Find where sections are defined and add after the last existing section:
```html
<section x-show="tab==='scheduler'" x-cloak>
  <div x-data="schedulerSection()" x-init="init()">

    <!-- Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 style="font-size:18px;font-weight:700">Scheduler</h2>
      <button class="btn btn-blue" @click="showForm=true;editJob=null;resetForm()">+ Nouveau job</button>
    </div>

    <!-- Job list table -->
    <div class="card">
      <table class="tbl">
        <thead>
          <tr>
            <th>Nom</th><th>Prochain déclenchement</th><th>Canaux</th>
            <th>Dernier statut</th><th>Actif</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <template x-for="job in jobs" :key="job.id">
            <tr>
              <td x-text="job.name"></td>
              <td x-text="job.next_run_time ? relativeTime(job.next_run_time) : '—'" class="mono"></td>
              <td><span x-text="(job.channels||[]).join(', ')" class="badge badge-blue"></span></td>
              <td>
                <span :class="{'badge-green':job.last_status==='ok','badge-red':job.last_status==='error','badge-yellow':job.last_status==='timeout','badge-muted':!job.last_status}" class="badge" x-text="job.last_status||'jamais'"></span>
              </td>
              <td>
                <input type="checkbox" :checked="job.enabled" @change="toggleJob(job, $event.target.checked)">
              </td>
              <td style="white-space:nowrap">
                <button class="btn btn-muted btn-sm" @click="openEdit(job)">Modifier</button>
                <button class="btn btn-blue btn-sm" @click="runNow(job)" :disabled="job.last_status==='running'">▶ Lancer</button>
                <button class="btn btn-muted btn-sm" @click="openHistory(job)">Historique</button>
                <button class="btn btn-red btn-sm" @click="deleteJob(job)" x-show="!job.system">Supprimer</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Create/Edit form panel -->
    <div x-show="showForm" class="card" style="margin-top:12px">
      <h3 x-text="editJob ? 'Modifier le job' : 'Nouveau job'"></h3>
      <div class="form-row">
        <label>Nom</label>
        <input type="text" x-model="form.name" style="flex:1">
      </div>
      <div class="form-row">
        <label>Cron</label>
        <input type="text" x-model="form.cron" placeholder="0 8 * * *" style="flex:1">
        <span x-text="nextRunHint" class="mono" style="color:var(--muted);font-size:11px"></span>
      </div>
      <div class="form-row">
        <label>Timeout (s)</label>
        <input type="number" x-model.number="form.timeout_s" min="10" max="300" style="width:80px">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">SECTIONS</label>
        <template x-for="sec in allSections" :key="sec.key">
          <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
            <input type="checkbox" :value="sec.key" x-model="form.sections"> <span x-text="sec.label"></span>
          </label>
        </template>
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">CANAUX</label>
        <template x-for="ch in allChannels" :key="ch">
          <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px">
            <input type="checkbox" :value="ch" x-model="form.channels"> <span x-text="ch"></span>
          </label>
        </template>
      </div>
      <div x-show="form.sections.includes('custom')" style="margin-bottom:8px">
        <label style="display:block;margin-bottom:4px;color:var(--muted);font-size:12px">PROMPT PERSONNALISÉ</label>
        <textarea x-model="form.prompt" rows="3" placeholder="Variables: {{date}} {{time}} {{hostname}} {{job_name}}"></textarea>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-blue" @click="saveJob()">Sauvegarder</button>
        <button class="btn btn-green" @click="testJob()" x-show="editJob">Tester maintenant</button>
        <button class="btn btn-muted" @click="showForm=false">Annuler</button>
      </div>
      <div x-show="testOutput" class="card" style="margin-top:8px;background:var(--input-bg)">
        <pre x-text="testOutput" style="white-space:pre-wrap;font-size:12px"></pre>
      </div>
    </div>

    <!-- History panel -->
    <div x-show="historyJob" class="card" style="margin-top:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h3>Historique — <span x-text="historyJob?.name"></span></h3>
        <button class="btn btn-muted btn-sm" @click="historyJob=null">✕ Fermer</button>
      </div>
      <table class="tbl">
        <thead><tr><th>Date</th><th>Durée</th><th>Statut</th><th>Canaux</th><th>Aperçu</th></tr></thead>
        <tbody>
          <template x-for="run in history" :key="run.id">
            <tr>
              <td x-text="run.started_at" class="mono"></td>
              <td x-text="run.duration_ms ? run.duration_ms+'ms' : '—'"></td>
              <td><span :class="{'badge-green':run.status==='ok','badge-red':run.status==='error','badge-yellow':run.status==='timeout'}" class="badge" x-text="run.status"></span></td>
              <td x-text="run.channels_ok ? JSON.stringify(JSON.parse(run.channels_ok)) : '—'" class="mono" style="font-size:11px"></td>
              <td x-text="run.output ? run.output.slice(0,150)+'…' : '—'" style="font-size:12px"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</section>
```

- [ ] **Step 4: Add schedulerSection() Alpine.js component**

Find the `<script>` block in admin_ui.py that defines the other Alpine.js components and add at the end:
```javascript
function schedulerSection() {
  return {
    jobs: [], showForm: false, editJob: null, historyJob: null, history: [],
    testOutput: '',
    form: {name:'', cron:'0 8 * * *', sections:[], channels:[], prompt:'', timeout_s:60},
    allSections: [
      {key:'system_health', label:'Santé système'},
      {key:'personal_notes', label:'Notes récentes'},
      {key:'topics', label:'Sujets (⚠ coût LLM)'},
      {key:'reminders', label:'Rappels'},
      {key:'weekly_summary', label:'Bilan hebdo'},
      {key:'custom', label:'Prompt personnalisé'},
    ],
    allChannels: ['ntfy', 'telegram', 'discord', 'whatsapp'],
    get nextRunHint() {
      try {
        // Simple display — server provides actual next_run_time
        return this.form.cron ? 'cron: ' + this.form.cron : '';
      } catch { return ''; }
    },
    async init() { await this.loadJobs(); },
    async loadJobs() {
      const r = await fetch('/api/scheduler/jobs');
      if (r.ok) this.jobs = await r.json();
    },
    resetForm() {
      this.form = {name:'', cron:'0 8 * * *', sections:[], channels:[], prompt:'', timeout_s:60};
      this.testOutput = '';
    },
    openEdit(job) {
      this.editJob = job;
      this.form = {name:job.name, cron:job.cron, sections:[...job.sections],
                   channels:[...job.channels], prompt:job.prompt||'', timeout_s:job.timeout_s};
      this.showForm = true;
    },
    async saveJob() {
      const url = this.editJob ? `/api/scheduler/jobs/${this.editJob.id}` : '/api/scheduler/jobs';
      const method = this.editJob ? 'PUT' : 'POST';
      const r = await fetch(url, {method, headers:{'Content-Type':'application/json'},
                                   body: JSON.stringify(this.form)});
      if (r.ok) { this.showForm = false; await this.loadJobs(); }
      else { const e = await r.json(); alert(e.detail || 'Erreur'); }
    },
    async testJob() {
      this.testOutput = 'Exécution en cours…';
      const r = await fetch(`/api/scheduler/jobs/${this.editJob.id}/run`, {method:'POST'});
      if (r.ok) {
        this.testOutput = 'Job déclenché. Résultat visible dans l\'historique dans quelques secondes.';
        setTimeout(() => this.loadJobs(), 3000);
      } else {
        const e = await r.json();
        this.testOutput = 'Erreur: ' + (e.detail || JSON.stringify(e));
      }
    },
    async runNow(job) {
      const r = await fetch(`/api/scheduler/jobs/${job.id}/run`, {method:'POST'});
      if (r.ok) { job.last_status = 'running'; }
      else { const e = await r.json(); alert(e.detail || 'Erreur'); }
    },
    async toggleJob(job, enabled) {
      await fetch(`/api/scheduler/jobs/${job.id}/toggle?enabled=${enabled}`, {method:'POST'});
      await this.loadJobs();
    },
    async deleteJob(job) {
      if (!confirm(`Supprimer "${job.name}" ?`)) return;
      const r = await fetch(`/api/scheduler/jobs/${job.id}`, {method:'DELETE'});
      if (r.ok) await this.loadJobs();
      else { const e = await r.json(); alert(e.detail || 'Erreur'); }
    },
    async openHistory(job) {
      this.historyJob = job;
      const r = await fetch(`/api/scheduler/jobs/${job.id}/history`);
      if (r.ok) this.history = await r.json();
    },
    relativeTime(iso) {
      const diff = new Date(iso) - new Date();
      const abs = Math.abs(diff);
      if (abs < 60000) return 'maintenant';
      if (abs < 3600000) return `dans ${Math.round(abs/60000)}min`;
      if (abs < 86400000) return `dans ${Math.round(abs/3600000)}h`;
      return `dans ${Math.round(abs/86400000)}j`;
    },
  };
}
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v
# Expected: all tests PASS
```

- [ ] **Step 6: Commit**

```bash
git add src/bridge/admin_ui.py
git commit -m "feat(scheduler): add Scheduler tab to Admin UI with full CRUD and history"
```

---

## Task 11: Final Integration Smoke Test

- [ ] **Step 1: Install new dependencies**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo/src/bridge
pip install apscheduler>=3.10 croniter>=2.0 sqlalchemy>=2.0
```

- [ ] **Step 2: Run all tests**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo
pytest tests/ -v
# Expected: all tests PASS
```

- [ ] **Step 3: Verify imports from bridge directory**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo/src/bridge
python -c "
from broadcast_notifier import BroadcastNotifier
from scheduler import SchedulerManager
from scheduler_executor import JobExecutor
from scheduler_api import router, init_scheduler_api
from scheduler_registry import JobRegistry
print('All imports OK')
"
```

- [ ] **Step 4: Run migration dry-run**

```bash
cd /c/Users/draey/Downloads/nanobot-stack-repo
python migrations/run_migrations.py --dry-run
# Expected: "Migration 11: 011_scheduler — would apply"
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(scheduler): complete scheduler & briefing system — all tests pass"
```
