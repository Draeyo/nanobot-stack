# Sub-projet H — Memory Decay & Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add memory decay (exponential confidence scoring with weekly cleanup) and a feedback loop (thumbs up/down → routing weight adjustment)

**Architecture:** MemoryDecayManager scans Qdrant permanent collections weekly, applies decay formula confidence * e^(-λ * days), deletes below threshold (with safety guard: abort if >20% would be deleted). FeedbackLearner reads feedback table, adjusts routing scores in routing_adjustments SQLite table, adaptive_router.py applies adjustments at score time.

**Tech Stack:** Qdrant (existing), SQLite (existing), APScheduler (existing), math (stdlib), FastAPI (existing)

---

## Context: Key Files

| File | Role |
|------|------|
| `src/bridge/adaptive_router.py` | `AdaptiveRouter.get_model_ranking()` — integration point for routing adjustments |
| `src/bridge/user_profile.py` | `record_preference_signal()` — append-only learning log pattern |
| `src/bridge/scheduler_registry.py` | `SYSTEM_JOBS` list + `JobRegistry.seed()` — pattern for adding system cron jobs |
| `src/bridge/feedback.py` | Existing `feedback.db` with `feedback` table (schema: chunk_id, collection, query, signal, created_at) |
| `migrations/015_backup_log.py` | Migration pattern: `VERSION`, `check()`, `migrate()`, WAL mode, `scheduler.db` |
| `src/bridge/app.py` | FastAPI app — router mounting pattern |

**DB notes:**
- The existing `feedback` table lives in `feedback.db` (not `rag.db`). Migration 016 will `ALTER TABLE` it in `feedback.db`.
- New tables `memory_decay_log` and `routing_adjustments` go in `feedback.db` to colocate learning data.
- Migration number is **016** (last existing: 015).

---

## Task 1 — Migration `migrations/018_memory_decay_feedback.py`

### What
Create `migrations/018_memory_decay_feedback.py` that:
1. Adds four columns to the existing `feedback` table in `feedback.db`
2. Creates `memory_decay_log` table with indexes
3. Creates `routing_adjustments` table

### Red — write the test first

Create `tests/test_migration_016.py`:

```python
"""Tests for migration 016 — memory_decay_log, routing_adjustments, feedback extension."""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))
sys.path.insert(0, str(Path(__file__).parent.parent / "migrations"))


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    # Pre-create feedback.db with existing feedback table (as feedback.py would)
    db = sqlite3.connect(str(tmp_path / "feedback.db"))
    db.execute("""CREATE TABLE feedback (
        chunk_id   TEXT NOT NULL,
        collection TEXT NOT NULL,
        query      TEXT NOT NULL,
        signal     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chunk_id, query, signal)
    )""")
    db.commit()
    db.close()
    return tmp_path


def test_check_returns_false_before_migration(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    assert m.check({}) is False


def test_migrate_creates_memory_decay_log(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "memory_decay_log" in tables
    db.close()


def test_migrate_creates_routing_adjustments(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "routing_adjustments" in tables
    db.close()


def test_migrate_adds_feedback_columns(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(feedback)").fetchall()}
    assert "query_type" in cols
    assert "model_used" in cols
    assert "was_helpful" in cols
    assert "correction_text" in cols
    db.close()


def test_check_returns_true_after_migration(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    assert m.check({}) is True


def test_migrate_is_idempotent(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    m.migrate({})  # second run must not raise


def test_memory_decay_log_schema(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(memory_decay_log)").fetchall()}
    assert cols == {"id", "collection", "point_id", "old_score", "new_score", "reason", "created_at"}
    db.close()


def test_routing_adjustments_schema(state_dir):
    import importlib
    m = importlib.import_module("018_memory_decay_feedback")
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "feedback.db"))
    cols = {r[1] for r in db.execute("PRAGMA table_info(routing_adjustments)").fetchall()}
    assert cols == {"query_type", "model_id", "adjustment", "feedback_count", "updated_at"}
    db.close()
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_migration_016.py -v
# Expected: ModuleNotFoundError for 018_memory_decay_feedback
```

### Green — implement

- [ ] Create `migrations/018_memory_decay_feedback.py`:

```python
"""018_memory_decay_feedback — memory decay log, routing adjustments, feedback extension."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 18

logger = logging.getLogger("migration.v16")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def _safe_add_column(db: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    except sqlite3.OperationalError:
        pass  # column already exists


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "feedback.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        return "memory_decay_log" in tables and "routing_adjustments" in tables
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "feedback.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        # Extend existing feedback table
        _safe_add_column(db, "feedback", "query_type",      "TEXT DEFAULT NULL")
        _safe_add_column(db, "feedback", "model_used",      "TEXT DEFAULT NULL")
        _safe_add_column(db, "feedback", "was_helpful",     "INTEGER DEFAULT NULL")
        _safe_add_column(db, "feedback", "correction_text", "TEXT DEFAULT NULL")

        # New table: memory_decay_log (append-only audit trail)
        db.execute("""
            CREATE TABLE IF NOT EXISTS memory_decay_log (
                id           TEXT PRIMARY KEY,
                collection   TEXT NOT NULL,
                point_id     TEXT NOT NULL,
                old_score    REAL NOT NULL,
                new_score    REAL NOT NULL,
                reason       TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_decay_log_collection
            ON memory_decay_log(collection)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_decay_log_created_at
            ON memory_decay_log(created_at)
        """)

        # New table: routing_adjustments
        db.execute("""
            CREATE TABLE IF NOT EXISTS routing_adjustments (
                query_type      TEXT NOT NULL,
                model_id        TEXT NOT NULL,
                adjustment      REAL NOT NULL DEFAULT 1.0,
                feedback_count  INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (query_type, model_id)
            )
        """)

        db.commit()
        logger.info("Migration 016: memory_decay_log, routing_adjustments created; feedback extended at %s", db_path)
    finally:
        db.close()
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_migration_016.py -v
# Expected: all 8 tests pass
```

### Commit
```
git add migrations/018_memory_decay_feedback.py tests/test_migration_016.py
git commit -m "feat(migration): add 016 — memory_decay_log, routing_adjustments, feedback extension"
```

---

## Task 2 — `MemoryDecayManager` skeleton + `score_point()`

### What
Create `src/bridge/memory_decay.py` with:
- `MemoryDecayManager.__init__()` — reads env vars, obtains DB path and Qdrant client
- `MEMORY_DECAY_ENABLED` guard (no-op pattern)
- `score_point(confidence: float, days_since_access: float) -> float` — pure math function
- `_now_iso() -> str` helper

### Red — write the test first

Create `tests/test_memory_decay.py` (partial, Task 2 section):

```python
"""Tests for MemoryDecayManager."""
from __future__ import annotations
import math
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


# ---------------------------------------------------------------------------
# Task 2 — score_point() math
# ---------------------------------------------------------------------------

class TestScorePointMath:
    """score_point(confidence, days_since_access) -> float"""

    def _make_manager(self, monkeypatch, tmp_path, lambda_val="0.01", threshold="0.1", enabled="true"):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", enabled)
        monkeypatch.setenv("MEMORY_DECAY_LAMBDA", lambda_val)
        monkeypatch.setenv("MEMORY_DECAY_THRESHOLD", threshold)
        monkeypatch.setenv("MEMORY_DECAY_COLLECTIONS", "memory_personal,conversation_summaries")
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        # Ensure migration ran
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
        db.commit()
        db.close()
        mock_qdrant = MagicMock()
        from memory_decay import MemoryDecayManager
        return MemoryDecayManager(qdrant_client=mock_qdrant)

    def test_decay_formula_math(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # confidence=1.0, days=70, lambda=0.01 → 1.0 * e^(-0.01*70) ≈ 0.4966
        result = mgr.score_point(1.0, 70.0)
        expected = 1.0 * math.exp(-0.01 * 70.0)
        assert abs(result - expected) < 0.0001

    def test_decay_with_zero_days(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # 0 days elapsed → multiplier = e^0 = 1.0 → score unchanged
        result = mgr.score_point(0.8, 0.0)
        assert abs(result - 0.8) < 0.0001

    def test_decay_half_life(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # λ=0.01, half-life = ln(2)/0.01 ≈ 69.3 days
        half_life = math.log(2) / 0.01
        result = mgr.score_point(1.0, half_life)
        assert abs(result - 0.5) < 0.01

    def test_score_clamped_to_zero_minimum(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # Extremely high days → score approaches 0, never negative
        result = mgr.score_point(1.0, 10000.0)
        assert result >= 0.0

    def test_score_clamped_to_one_maximum(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # confidence already 1.0, days=0 → should not exceed 1.0
        result = mgr.score_point(1.0, 0.0)
        assert result <= 1.0

    def test_disabled_score_point_still_works(self, monkeypatch, tmp_path):
        # score_point is pure math — it works regardless of ENABLED flag
        mgr = self._make_manager(monkeypatch, tmp_path, enabled="false")
        result = mgr.score_point(0.6, 30.0)
        expected = 0.6 * math.exp(-0.01 * 30.0)
        assert abs(result - expected) < 0.0001
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py::TestScorePointMath -v
# Expected: ModuleNotFoundError for memory_decay
```

### Green — implement

- [ ] Create `src/bridge/memory_decay.py`:

```python
"""MemoryDecayManager — exponential confidence decay for Qdrant permanent collections.

Opt-in via MEMORY_DECAY_ENABLED=true. When disabled, all methods are no-ops.
"""
from __future__ import annotations

import logging
import math
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.memory_decay")

# ---------------------------------------------------------------------------
# Configuration (env vars)
# ---------------------------------------------------------------------------
MEMORY_DECAY_ENABLED: bool = os.getenv("MEMORY_DECAY_ENABLED", "false").lower() == "true"
MEMORY_DECAY_LAMBDA: float = float(os.getenv("MEMORY_DECAY_LAMBDA", "0.01"))
MEMORY_DECAY_THRESHOLD: float = float(os.getenv("MEMORY_DECAY_THRESHOLD", "0.1"))
MEMORY_DECAY_COLLECTIONS: list[str] = [
    c.strip() for c in
    os.getenv("MEMORY_DECAY_COLLECTIONS", "memory_personal,conversation_summaries").split(",")
    if c.strip()
]
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

_NOOP_SCAN_RESULT: dict[str, Any] = {"scanned": 0, "updated": 0, "deleted": 0, "collections": []}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryDecayManager:
    """Manages exponential confidence decay for Qdrant permanent memory collections."""

    def __init__(self, qdrant_client: Any = None) -> None:
        self._qdrant = qdrant_client
        self._db_path = STATE_DIR / "feedback.db"

    # ------------------------------------------------------------------
    # Pure math helper — works regardless of ENABLED flag
    # ------------------------------------------------------------------

    def score_point(self, confidence: float, days_since_access: float) -> float:
        """Apply exponential decay formula: confidence * e^(-λ * days).

        Args:
            confidence: Current confidence score (0.0–1.0).
            days_since_access: Days elapsed since last confirmed access.

        Returns:
            New confidence score, clamped to [0.0, 1.0].
        """
        new_score = confidence * math.exp(-MEMORY_DECAY_LAMBDA * days_since_access)
        return max(0.0, min(1.0, new_score))

    # ------------------------------------------------------------------
    # Internal DB helper
    # ------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _log_decay(
        self,
        db: sqlite3.Connection,
        collection: str,
        point_id: str,
        old_score: float,
        new_score: float,
        reason: str,
    ) -> None:
        db.execute(
            "INSERT INTO memory_decay_log (id, collection, point_id, old_score, new_score, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), collection, str(point_id), old_score, new_score, reason, _now_iso()),
        )
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py::TestScorePointMath -v
# Expected: 6 tests pass
```

### Commit
```
git add src/bridge/memory_decay.py tests/test_memory_decay.py
git commit -m "feat(memory-decay): add MemoryDecayManager skeleton with score_point() decay formula"
```

---

## Task 3 — `run_decay_scan(collection_name)`

### What
Implement `MemoryDecayManager.run_decay_scan(collection_name: str | None = None)`:
- No-op if `MEMORY_DECAY_ENABLED=false`
- Scrolls Qdrant collection(s) in batches of 100
- For each point: computes `score_point()` using payload `confidence_score` + `last_accessed` (with defaults for missing fields)
- Safety guard: if >20% of points in a collection would be deleted, abort with `bulk_delete_guard` error (no deletions applied)
- Deletes points below `MEMORY_DECAY_THRESHOLD` — logs `reason='threshold_delete'` to DB **before** the Qdrant DELETE
- Updates payload for points above threshold (if delta > 0.001), logs if delta > 0.05 (`reason='decay'`)
- Returns `{"scanned": N, "updated": M, "deleted": K, "collections": [...]}`

### Red — append to `tests/test_memory_decay.py`

```python
# ---------------------------------------------------------------------------
# Task 3 — run_decay_scan()
# ---------------------------------------------------------------------------

class TestRunDecayScan:

    def _make_manager(self, monkeypatch, tmp_path, enabled="true"):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", enabled)
        monkeypatch.setenv("MEMORY_DECAY_LAMBDA", "0.01")
        monkeypatch.setenv("MEMORY_DECAY_THRESHOLD", "0.1")
        monkeypatch.setenv("MEMORY_DECAY_COLLECTIONS", "memory_personal")
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        db.execute("""CREATE TABLE IF NOT EXISTS memory_decay_log (
            id TEXT PRIMARY KEY, collection TEXT NOT NULL, point_id TEXT NOT NULL,
            old_score REAL NOT NULL, new_score REAL NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        db.commit()
        db.close()
        mock_qdrant = MagicMock()
        from memory_decay import MemoryDecayManager
        return MemoryDecayManager(qdrant_client=mock_qdrant), mock_qdrant

    def _make_point(self, point_id, confidence=0.9, last_accessed=None, days_old=10):
        from datetime import datetime, timezone, timedelta
        pt = MagicMock()
        pt.id = point_id
        accessed = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        pt.payload = {
            "confidence_score": confidence,
            "last_accessed": last_accessed or accessed,
        }
        return pt

    def test_disabled_returns_noop(self, monkeypatch, tmp_path):
        mgr, _ = self._make_manager(monkeypatch, tmp_path, enabled="false")
        result = mgr.run_decay_scan()
        assert result == {"scanned": 0, "updated": 0, "deleted": 0, "collections": []}

    def test_threshold_triggers_delete(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # Point with confidence 0.05 (below threshold 0.1) and 0 days since access
        # → score_point(0.05, 0) = 0.05 < 0.1 → should be deleted
        low_pt = self._make_point("pt-low", confidence=0.05, days_old=0)
        # Qdrant scroll returns one page then stops
        mock_qdrant.scroll.return_value = ([low_pt], None)
        mgr.run_decay_scan("memory_personal")
        mock_qdrant.delete.assert_called_once()

    def test_threshold_no_delete_above(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # Point with confidence 0.9, 1 day old → score > 0.1 → no delete
        high_pt = self._make_point("pt-high", confidence=0.9, days_old=1)
        mock_qdrant.scroll.return_value = ([high_pt], None)
        mgr.run_decay_scan("memory_personal")
        mock_qdrant.delete.assert_not_called()

    def test_audit_log_created_before_delete(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        low_pt = self._make_point("pt-audit", confidence=0.05, days_old=0)
        mock_qdrant.scroll.return_value = ([low_pt], None)

        delete_called_order = []

        def track_delete(*args, **kwargs):
            # Check that log entry exists before delete
            db = sqlite3.connect(str(tmp_path / "feedback.db"))
            rows = db.execute(
                "SELECT reason FROM memory_decay_log WHERE point_id=?", ("pt-audit",)
            ).fetchall()
            db.close()
            delete_called_order.append(("delete", [r[0] for r in rows]))

        mock_qdrant.delete.side_effect = track_delete
        mgr.run_decay_scan("memory_personal")
        assert delete_called_order, "delete was never called"
        reasons_at_delete_time = delete_called_order[0][1]
        assert "threshold_delete" in reasons_at_delete_time

    def test_bulk_delete_guard(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # 4 points all below threshold (100% → exceeds 20% guard)
        low_pts = [self._make_point(f"pt-{i}", confidence=0.05, days_old=0) for i in range(4)]
        mock_qdrant.scroll.return_value = (low_pts, None)
        result = mgr.run_decay_scan("memory_personal")
        mock_qdrant.delete.assert_not_called()
        assert result.get("bulk_delete_guard") is True or "error" in result or result.get("deleted") == 0

    def test_missing_payload_defaults(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        pt = MagicMock()
        pt.id = "pt-no-payload"
        pt.payload = {}  # no confidence_score, no last_accessed
        mock_qdrant.scroll.return_value = ([pt], None)
        # Must not raise — defaults are applied (confidence=1.0, last_accessed=now)
        result = mgr.run_decay_scan("memory_personal")
        assert result["scanned"] >= 1

    def test_scan_returns_correct_counts(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # 1 point above threshold (will be updated), scroll returns 1 page
        pt = self._make_point("pt-update", confidence=0.9, days_old=100)
        mock_qdrant.scroll.return_value = ([pt], None)
        result = mgr.run_decay_scan("memory_personal")
        assert result["scanned"] == 1
        assert "deleted" in result
        assert "updated" in result
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py::TestRunDecayScan -v
# Expected: AttributeError — run_decay_scan not implemented
```

### Green — implement

- [ ] Add `run_decay_scan()` to `MemoryDecayManager` in `src/bridge/memory_decay.py`:

```python
    def run_decay_scan(self, collection_name: str | None = None) -> dict[str, Any]:
        """Scan Qdrant collection(s), apply decay, delete below threshold.

        Safety guard: if >20% of points in a collection would be deleted,
        abort without deleting and return bulk_delete_guard=True.

        Returns:
            Dict with keys: scanned, updated, deleted, collections.
            On guard trigger: also includes bulk_delete_guard=True.
        """
        if not MEMORY_DECAY_ENABLED:
            return dict(_NOOP_SCAN_RESULT)

        collections = [collection_name] if collection_name else MEMORY_DECAY_COLLECTIONS
        total_scanned = total_updated = total_deleted = 0
        result_collections = []

        for coll in collections:
            scanned = updated = deleted = 0
            to_delete: list[str] = []
            to_update: list[tuple[str, float, str]] = []  # (point_id, new_score, last_accessed)
            log_entries: list[tuple] = []  # buffered for pre-delete audit

            # --- Scroll all points ---
            offset = None
            while True:
                try:
                    points, next_offset = self._qdrant.scroll(
                        collection_name=coll,
                        limit=100,
                        offset=offset,
                        with_payload=True,
                    )
                except Exception as exc:
                    logger.error("Qdrant scroll failed for %s: %s", coll, exc)
                    break

                for pt in points:
                    scanned += 1
                    payload = pt.payload or {}
                    old_score: float = float(payload.get("confidence_score", 1.0))
                    last_accessed_str: str = payload.get(
                        "last_accessed", payload.get("created_at", _now_iso())
                    )
                    try:
                        last_accessed_dt = datetime.fromisoformat(last_accessed_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        last_accessed_dt = datetime.now(timezone.utc)

                    now = datetime.now(timezone.utc)
                    days_elapsed = (now - last_accessed_dt).total_seconds() / 86400.0
                    new_score = self.score_point(old_score, days_elapsed)
                    delta = abs(new_score - old_score)

                    if delta < 0.001:
                        continue  # skip micro-variations

                    if new_score < MEMORY_DECAY_THRESHOLD:
                        to_delete.append(str(pt.id))
                        log_entries.append((
                            str(uuid.uuid4()), coll, str(pt.id),
                            old_score, 0.0, "threshold_delete", _now_iso(),
                        ))
                    else:
                        to_update.append((str(pt.id), new_score, last_accessed_str))
                        if delta > 0.05:
                            log_entries.append((
                                str(uuid.uuid4()), coll, str(pt.id),
                                old_score, new_score, "decay", _now_iso(),
                            ))

                if not next_offset:
                    break
                offset = next_offset

            # --- Bulk delete guard ---
            if scanned > 0 and len(to_delete) / scanned > 0.20:
                logger.warning(
                    "bulk_delete_guard triggered for %s: %d/%d points below threshold",
                    coll, len(to_delete), scanned,
                )
                result_collections.append({
                    "collection": coll, "scanned": scanned,
                    "updated": 0, "deleted": 0, "bulk_delete_guard": True,
                })
                total_scanned += scanned
                return {
                    "scanned": total_scanned, "updated": 0, "deleted": 0,
                    "collections": result_collections, "bulk_delete_guard": True,
                }

            # --- Write audit log BEFORE deletions ---
            if log_entries:
                db = self._db()
                try:
                    db.executemany(
                        "INSERT INTO memory_decay_log "
                        "(id, collection, point_id, old_score, new_score, reason, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        log_entries,
                    )
                    db.commit()
                finally:
                    db.close()

            # --- Apply deletions ---
            for point_id in to_delete:
                try:
                    from qdrant_client.models import PointIdsList
                    self._qdrant.delete(
                        collection_name=coll,
                        points_selector=PointIdsList(points=[point_id]),
                    )
                    deleted += 1
                except Exception as exc:
                    logger.error("Failed to delete point %s from %s: %s", point_id, coll, exc)

            # --- Apply payload updates ---
            for point_id, new_score, last_accessed in to_update:
                try:
                    self._qdrant.set_payload(
                        collection_name=coll,
                        payload={"confidence_score": new_score, "last_accessed": last_accessed},
                        points=[point_id],
                    )
                    updated += 1
                except Exception as exc:
                    logger.error("Failed to update payload for %s in %s: %s", point_id, coll, exc)

            total_scanned += scanned
            total_updated += updated
            total_deleted += deleted
            result_collections.append({
                "collection": coll, "scanned": scanned,
                "updated": updated, "deleted": deleted,
            })

        return {
            "scanned": total_scanned,
            "updated": total_updated,
            "deleted": total_deleted,
            "collections": result_collections,
        }
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py::TestRunDecayScan -v
# Expected: all 7 tests pass
```

### Commit
```
git add src/bridge/memory_decay.py tests/test_memory_decay.py
git commit -m "feat(memory-decay): implement run_decay_scan() with bulk_delete_guard and audit log"
```

---

## Task 4 — `confirm_access()` and `forget()`

### What
Implement:
- `confirm_access(collection, point_id)` — resets `confidence_score=1.0`, updates `last_accessed=now`, logs `reason='confirm'`
- `forget(collection, point_id)` — logs `reason='explicit_forget'`, deletes point from Qdrant, returns result dict

### Red — append to `tests/test_memory_decay.py`

```python
# ---------------------------------------------------------------------------
# Task 4 — confirm_access() and forget()
# ---------------------------------------------------------------------------

class TestConfirmAndForget:

    def _make_manager(self, monkeypatch, tmp_path, enabled="true"):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", enabled)
        monkeypatch.setenv("MEMORY_DECAY_LAMBDA", "0.01")
        monkeypatch.setenv("MEMORY_DECAY_THRESHOLD", "0.1")
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        db.execute("""CREATE TABLE IF NOT EXISTS memory_decay_log (
            id TEXT PRIMARY KEY, collection TEXT NOT NULL, point_id TEXT NOT NULL,
            old_score REAL NOT NULL, new_score REAL NOT NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        db.commit()
        db.close()
        mock_qdrant = MagicMock()
        from memory_decay import MemoryDecayManager
        return MemoryDecayManager(qdrant_client=mock_qdrant), mock_qdrant

    def test_confirm_access_resets_score(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        mock_qdrant.retrieve.return_value = [
            MagicMock(payload={"confidence_score": 0.4, "last_accessed": "2025-01-01T00:00:00+00:00"})
        ]
        mgr.confirm_access("memory_personal", "pt-abc")
        mock_qdrant.set_payload.assert_called_once()
        call_kwargs = mock_qdrant.set_payload.call_args
        payload = call_kwargs.kwargs.get("payload") or call_kwargs[1].get("payload") or call_kwargs[0][1]
        assert payload["confidence_score"] == 1.0

    def test_confirm_access_logs_confirm_reason(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        mock_qdrant.retrieve.return_value = [
            MagicMock(payload={"confidence_score": 0.4, "last_accessed": "2025-01-01T00:00:00+00:00"})
        ]
        mgr.confirm_access("memory_personal", "pt-abc")
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        row = db.execute(
            "SELECT reason, new_score FROM memory_decay_log WHERE point_id=?", ("pt-abc",)
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "confirm"
        assert row[1] == 1.0

    def test_confirm_access_noop_when_disabled(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path, enabled="false")
        mgr.confirm_access("memory_personal", "pt-xyz")
        mock_qdrant.retrieve.assert_not_called()
        mock_qdrant.set_payload.assert_not_called()

    def test_forget_logs_and_deletes(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        mock_qdrant.retrieve.return_value = [
            MagicMock(payload={"confidence_score": 0.6})
        ]
        result = mgr.forget("memory_personal", "pt-forget-me")
        # Must log before delete — check log entry
        db = sqlite3.connect(str(tmp_path / "feedback.db"))
        row = db.execute(
            "SELECT reason, new_score FROM memory_decay_log WHERE point_id=?", ("pt-forget-me",)
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "explicit_forget"
        assert row[1] == 0.0
        # Must call delete
        mock_qdrant.delete.assert_called_once()
        assert result["deleted"] is True

    def test_forget_returns_correct_shape(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        mock_qdrant.retrieve.return_value = [MagicMock(payload={})]
        result = mgr.forget("memory_personal", "pt-shape")
        assert "deleted" in result
        assert "collection" in result
        assert "point_id" in result

    def test_disabled_noop_all_methods(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path, enabled="false")
        scan_result = mgr.run_decay_scan()
        assert scan_result == {"scanned": 0, "updated": 0, "deleted": 0, "collections": []}
        mgr.confirm_access("memory_personal", "x")
        mock_qdrant.retrieve.assert_not_called()
        mock_qdrant.set_payload.assert_not_called()
        mock_qdrant.delete.assert_not_called()
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py::TestConfirmAndForget -v
# Expected: AttributeError — confirm_access/forget not implemented
```

### Green — implement

- [ ] Add `confirm_access()` and `forget()` to `MemoryDecayManager` in `src/bridge/memory_decay.py`:

```python
    def confirm_access(self, collection: str, point_id: str) -> None:
        """Reset confidence_score to 1.0 and update last_accessed after confirmed retrieval use.

        No-op if MEMORY_DECAY_ENABLED=false.
        """
        if not MEMORY_DECAY_ENABLED:
            return

        try:
            points = self._qdrant.retrieve(
                collection_name=collection, ids=[point_id], with_payload=True
            )
        except Exception as exc:
            logger.warning("confirm_access: failed to retrieve %s from %s: %s", point_id, collection, exc)
            return

        old_score = 1.0
        if points:
            old_score = float((points[0].payload or {}).get("confidence_score", 1.0))

        now = _now_iso()
        db = self._db()
        try:
            self._log_decay(db, collection, str(point_id), old_score, 1.0, "confirm")
            db.commit()
        finally:
            db.close()

        try:
            self._qdrant.set_payload(
                collection_name=collection,
                payload={"confidence_score": 1.0, "last_accessed": now},
                points=[point_id],
            )
        except Exception as exc:
            logger.warning("confirm_access: failed to update payload for %s: %s", point_id, exc)

    def forget(self, collection: str, point_id: str) -> dict[str, Any]:
        """Explicitly delete a memory point with full audit trail.

        Logs reason='explicit_forget' BEFORE the Qdrant delete.

        Returns:
            {"deleted": True, "collection": ..., "point_id": ...}
        """
        try:
            points = self._qdrant.retrieve(
                collection_name=collection, ids=[point_id], with_payload=True
            )
        except Exception as exc:
            logger.warning("forget: failed to retrieve %s from %s: %s", point_id, collection, exc)
            points = []

        old_score = 1.0
        if points:
            old_score = float((points[0].payload or {}).get("confidence_score", 1.0))

        db = self._db()
        try:
            self._log_decay(db, collection, str(point_id), old_score, 0.0, "explicit_forget")
            db.commit()
        finally:
            db.close()

        try:
            from qdrant_client.models import PointIdsList
            self._qdrant.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=[point_id]),
            )
        except Exception as exc:
            logger.error("forget: Qdrant delete failed for %s: %s", point_id, exc)

        return {"deleted": True, "collection": collection, "point_id": str(point_id)}
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py -v
# Expected: all memory decay tests pass
```

### Commit
```
git add src/bridge/memory_decay.py tests/test_memory_decay.py
git commit -m "feat(memory-decay): implement confirm_access() and forget() with pre-delete audit logging"
```

---

## Task 5 — `FeedbackLearner` skeleton + `record_feedback()`

### What
Create `src/bridge/feedback_learner.py` with:
- `FeedbackLearner.__init__()` — reads env vars, DB path
- `FEEDBACK_LEARNING_ENABLED` guard
- `record_feedback(query_id, query_type, model_used, was_helpful, correction_text)` — UPDATE existing feedback row; raise `ValueError` if `query_id` not found
- Triggers `analyze_and_apply()` as asyncio task if `FEEDBACK_LEARNING_ENABLED=true`

### Red — create `tests/test_feedback_learner.py`

```python
"""Tests for FeedbackLearner."""
from __future__ import annotations
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
    monkeypatch.setenv("FEEDBACK_WINDOW_DAYS", "7")
    monkeypatch.setenv("FEEDBACK_MIN_SAMPLES", "3")
    dbp = tmp_path / "feedback.db"
    db = sqlite3.connect(str(dbp))
    db.execute("""CREATE TABLE feedback (
        chunk_id   TEXT NOT NULL,
        collection TEXT NOT NULL,
        query      TEXT NOT NULL,
        signal     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        query_type      TEXT DEFAULT NULL,
        model_used      TEXT DEFAULT NULL,
        was_helpful     INTEGER DEFAULT NULL,
        correction_text TEXT DEFAULT NULL,
        PRIMARY KEY (chunk_id, query, signal)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS routing_adjustments (
        query_type TEXT NOT NULL, model_id TEXT NOT NULL,
        adjustment REAL NOT NULL DEFAULT 1.0,
        feedback_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY (query_type, model_id)
    )""")
    db.commit()
    db.close()
    return tmp_path


def _insert_feedback_row(db_path, chunk_id, query_type=None, model_used=None,
                          was_helpful=None, days_ago=0):
    now = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    db = sqlite3.connect(str(db_path / "feedback.db"))
    db.execute(
        "INSERT INTO feedback (chunk_id, collection, query, signal, created_at, query_type, model_used, was_helpful) "
        "VALUES (?, 'col', 'q', 'positive', ?, ?, ?, ?)",
        (chunk_id, now, query_type, model_used, was_helpful),
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Task 5 — record_feedback()
# ---------------------------------------------------------------------------

class TestRecordFeedback:

    def _make_learner(self, db_path):
        from feedback_learner import FeedbackLearner
        return FeedbackLearner()

    def test_record_feedback_updates_table(self, db_path):
        _insert_feedback_row(db_path, "qid-001")
        learner = self._make_learner(db_path)
        learner.record_feedback("qid-001", "code", "gpt-4o-mini", was_helpful=0, correction_text="wrong")
        db = sqlite3.connect(str(db_path / "feedback.db"))
        row = db.execute(
            "SELECT query_type, model_used, was_helpful, correction_text FROM feedback WHERE chunk_id=?",
            ("qid-001",)
        ).fetchone()
        db.close()
        assert row[0] == "code"
        assert row[1] == "gpt-4o-mini"
        assert row[2] == 0
        assert row[3] == "wrong"

    def test_record_feedback_unknown_query_id_raises(self, db_path):
        learner = self._make_learner(db_path)
        with pytest.raises(ValueError, match="query_id"):
            learner.record_feedback("nonexistent-id", "code", "gpt-4o", was_helpful=1)

    def test_record_feedback_positive(self, db_path):
        _insert_feedback_row(db_path, "qid-pos")
        learner = self._make_learner(db_path)
        learner.record_feedback("qid-pos", "factual", "claude-3-haiku", was_helpful=1)
        db = sqlite3.connect(str(db_path / "feedback.db"))
        row = db.execute("SELECT was_helpful FROM feedback WHERE chunk_id=?", ("qid-pos",)).fetchone()
        db.close()
        assert row[0] == 1

    def test_record_feedback_no_adjust_when_disabled(self, db_path, monkeypatch):
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
        _insert_feedback_row(db_path, "qid-disabled")
        learner = self._make_learner(db_path)
        # Should not call analyze_and_apply (no routing table changes)
        learner.record_feedback("qid-disabled", "code", "gpt-4o-mini", was_helpful=0)
        db = sqlite3.connect(str(db_path / "feedback.db"))
        adj_count = db.execute("SELECT COUNT(*) FROM routing_adjustments").fetchone()[0]
        db.close()
        assert adj_count == 0
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py::TestRecordFeedback -v
# Expected: ModuleNotFoundError for feedback_learner
```

### Green — implement

- [ ] Create `src/bridge/feedback_learner.py`:

```python
"""FeedbackLearner — translates thumbs up/down feedback into routing score adjustments.

Opt-in via FEEDBACK_LEARNING_ENABLED=true. Feedback is always recorded; adjustments
are only applied when the feature is enabled.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.feedback_learner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FEEDBACK_LEARNING_ENABLED: bool = os.getenv("FEEDBACK_LEARNING_ENABLED", "false").lower() == "true"
FEEDBACK_WINDOW_DAYS: int = int(os.getenv("FEEDBACK_WINDOW_DAYS", "7"))
FEEDBACK_MIN_SAMPLES: int = int(os.getenv("FEEDBACK_MIN_SAMPLES", "3"))
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

ADJUSTMENT_MIN = 0.5   # hard floor — never penalise below 50%
ADJUSTMENT_MAX = 1.5   # hard ceiling — never bonus above 150%


@dataclass
class RoutingAdjustment:
    query_type: str
    model_id: str
    adjustment: float
    feedback_count: int
    kind: str  # 'penalty' | 'bonus'


class FeedbackLearner:
    """Analyses user feedback and adjusts routing weights in routing_adjustments table."""

    def __init__(self) -> None:
        self._db_path = STATE_DIR / "feedback.db"

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # record_feedback
    # ------------------------------------------------------------------

    def record_feedback(
        self,
        query_id: str,
        query_type: str,
        model_used: str,
        was_helpful: int | bool,
        correction_text: str | None = None,
    ) -> None:
        """Update an existing feedback row with LLM routing metadata.

        Args:
            query_id: The chunk_id (primary key) of the existing feedback row.
            query_type: Query classification (e.g. 'code', 'factual').
            model_used: LiteLLM model identifier that generated the response.
            was_helpful: 1/True = positive, 0/False = negative.
            correction_text: Optional free-text correction from user.

        Raises:
            ValueError: If query_id does not exist in the feedback table.
        """
        db = self._db()
        try:
            existing = db.execute(
                "SELECT chunk_id FROM feedback WHERE chunk_id=?", (query_id,)
            ).fetchone()
            if not existing:
                raise ValueError(f"query_id '{query_id}' not found in feedback table")

            helpful_int = 1 if was_helpful else 0
            db.execute(
                """UPDATE feedback
                   SET query_type=?, model_used=?, was_helpful=?, correction_text=?
                   WHERE chunk_id=?""",
                (query_type, model_used, helpful_int, correction_text, query_id),
            )
            db.commit()
        finally:
            db.close()

        if FEEDBACK_LEARNING_ENABLED:
            try:
                adjustments = self.analyze_recent_feedback()
                if adjustments:
                    self.apply_adjustments(adjustments)
            except Exception as exc:
                logger.warning("analyze_and_apply failed: %s", exc)
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py::TestRecordFeedback -v
# Expected: 4 tests pass
```

### Commit
```
git add src/bridge/feedback_learner.py tests/test_feedback_learner.py
git commit -m "feat(feedback-learner): add FeedbackLearner skeleton with record_feedback()"
```

---

## Task 6 — `analyze_recent_feedback()` and `apply_adjustments()`

### What
Implement:
- `analyze_recent_feedback(window_days=None)` — aggregate feedback by (query_type, model_id) over rolling window; return `list[RoutingAdjustment]`
- `apply_adjustments(adjustments)` — upsert `routing_adjustments` table, update `UserProfile.learning_log` if delta > 0.1

### Red — append to `tests/test_feedback_learner.py`

```python
# ---------------------------------------------------------------------------
# Task 6 — analyze_recent_feedback() and apply_adjustments()
# ---------------------------------------------------------------------------

class TestAnalyzeAndApply:

    def _make_learner(self, db_path, monkeypatch, learning_enabled="false"):
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", learning_enabled)
        monkeypatch.setenv("FEEDBACK_MIN_SAMPLES", "3")
        monkeypatch.setenv("FEEDBACK_WINDOW_DAYS", "7")
        from feedback_learner import FeedbackLearner
        return FeedbackLearner()

    def _insert_n_feedbacks(self, db_path, query_type, model, was_helpful, n, days_ago=1):
        for i in range(n):
            _insert_feedback_row(
                db_path, f"qid-{query_type}-{model}-{i}",
                query_type=query_type, model_used=model,
                was_helpful=was_helpful, days_ago=days_ago,
            )

    def test_analyze_triggers_penalty_at_min_samples(self, db_path, monkeypatch):
        self._insert_n_feedbacks(db_path, "code", "gpt-4o-mini", was_helpful=0, n=3)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback()
        penalties = [a for a in adjustments if a.kind == "penalty"]
        assert len(penalties) >= 1
        assert penalties[0].query_type == "code"
        assert penalties[0].model_id == "gpt-4o-mini"
        assert penalties[0].adjustment < 1.0

    def test_analyze_no_penalty_below_min_samples(self, db_path, monkeypatch):
        self._insert_n_feedbacks(db_path, "code", "gpt-4o-mini", was_helpful=0, n=2)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback()
        penalties = [a for a in adjustments if a.kind == "penalty"]
        assert len(penalties) == 0

    def test_analyze_triggers_bonus_positive_dominance(self, db_path, monkeypatch):
        self._insert_n_feedbacks(db_path, "factual", "claude-3-haiku", was_helpful=1, n=4)
        self._insert_n_feedbacks(db_path, "factual", "claude-3-haiku", was_helpful=0, n=1)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback()
        bonuses = [a for a in adjustments if a.kind == "bonus"]
        assert len(bonuses) >= 1

    def test_feedback_window_filter(self, db_path, monkeypatch):
        # Feedback older than window_days+1 should be excluded
        self._insert_n_feedbacks(db_path, "code", "gpt-4o", was_helpful=0, n=5, days_ago=9)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback(window_days=7)
        assert len(adjustments) == 0

    def test_adjustment_lower_bound(self, db_path, monkeypatch):
        # Even with many negatives, adjustment should not drop below 0.5
        self._insert_n_feedbacks(db_path, "code", "gpt-4o-mini", was_helpful=0, n=20)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback()
        for a in adjustments:
            assert a.adjustment >= 0.5

    def test_adjustment_upper_bound(self, db_path, monkeypatch):
        # Even with many positives, adjustment should not exceed 1.5
        self._insert_n_feedbacks(db_path, "factual", "claude-3-opus", was_helpful=1, n=30)
        learner = self._make_learner(db_path, monkeypatch)
        adjustments = learner.analyze_recent_feedback()
        for a in adjustments:
            assert a.adjustment <= 1.5

    def test_apply_adjustments_upsert(self, db_path, monkeypatch):
        learner = self._make_learner(db_path, monkeypatch)
        from feedback_learner import RoutingAdjustment
        adj = RoutingAdjustment("code", "gpt-4o-mini", 0.8, 3, "penalty")
        learner.apply_adjustments([adj])
        db = sqlite3.connect(str(db_path / "feedback.db"))
        row = db.execute(
            "SELECT adjustment, feedback_count FROM routing_adjustments WHERE query_type=? AND model_id=?",
            ("code", "gpt-4o-mini")
        ).fetchone()
        db.close()
        assert row is not None
        assert abs(row[0] - 0.8) < 0.001
        assert row[1] == 3

    def test_apply_adjustments_updates_timestamp(self, db_path, monkeypatch):
        learner = self._make_learner(db_path, monkeypatch)
        from feedback_learner import RoutingAdjustment
        adj = RoutingAdjustment("analysis", "gpt-4o", 1.2, 5, "bonus")
        learner.apply_adjustments([adj])
        db = sqlite3.connect(str(db_path / "feedback.db"))
        row = db.execute(
            "SELECT updated_at FROM routing_adjustments WHERE query_type=? AND model_id=?",
            ("analysis", "gpt-4o")
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0]  # non-empty timestamp

    def test_learning_log_updated_on_large_delta(self, db_path, monkeypatch):
        learner = self._make_learner(db_path, monkeypatch)
        from feedback_learner import RoutingAdjustment
        adj = RoutingAdjustment("code", "gpt-4o-mini", 0.7, 3, "penalty")
        # Start from 1.0 default → delta = 0.3 > 0.1
        with patch("feedback_learner.record_preference_signal") as mock_signal:
            learner.apply_adjustments([adj])
            mock_signal.assert_called_once()

    def test_learning_log_not_updated_small_delta(self, db_path, monkeypatch):
        learner = self._make_learner(db_path, monkeypatch)
        from feedback_learner import RoutingAdjustment
        # First upsert to set baseline to 0.95
        db = sqlite3.connect(str(db_path / "feedback.db"))
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO routing_adjustments (query_type, model_id, adjustment, feedback_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)", ("code", "gpt-4o", 0.95, 2, now)
        )
        db.commit()
        db.close()
        adj = RoutingAdjustment("code", "gpt-4o", 0.97, 3, "bonus")  # delta = 0.02 < 0.1
        with patch("feedback_learner.record_preference_signal") as mock_signal:
            learner.apply_adjustments([adj])
            mock_signal.assert_not_called()

    def test_feedback_learning_disabled_no_adjustment(self, db_path, monkeypatch):
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
        _insert_feedback_row(db_path, "qid-nolearn")
        from feedback_learner import FeedbackLearner
        learner = FeedbackLearner()
        learner.record_feedback("qid-nolearn", "code", "gpt-4o-mini", was_helpful=0)
        db = sqlite3.connect(str(db_path / "feedback.db"))
        adj_count = db.execute("SELECT COUNT(*) FROM routing_adjustments").fetchone()[0]
        db.close()
        assert adj_count == 0
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py::TestAnalyzeAndApply -v
# Expected: AttributeError — analyze_recent_feedback/apply_adjustments not implemented
```

### Green — implement

- [ ] Add `analyze_recent_feedback()` and `apply_adjustments()` to `FeedbackLearner` in `src/bridge/feedback_learner.py`:

```python
    def analyze_recent_feedback(self, window_days: int | None = None) -> list[RoutingAdjustment]:
        """Aggregate feedback over a rolling window and compute routing adjustments.

        Args:
            window_days: Override FEEDBACK_WINDOW_DAYS env var (for testing).

        Returns:
            List of RoutingAdjustment objects (penalties and bonuses).
        """
        window = window_days if window_days is not None else FEEDBACK_WINDOW_DAYS
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).isoformat()

        db = self._db()
        try:
            rows = db.execute(
                """SELECT query_type, model_used,
                          SUM(CASE WHEN was_helpful=0 THEN 1 ELSE 0 END) AS neg,
                          SUM(CASE WHEN was_helpful=1 THEN 1 ELSE 0 END) AS pos,
                          COUNT(*) AS total
                   FROM feedback
                   WHERE created_at >= ?
                     AND query_type IS NOT NULL
                     AND model_used IS NOT NULL
                     AND was_helpful IS NOT NULL
                   GROUP BY query_type, model_used""",
                (cutoff,),
            ).fetchall()

            # Load current adjustments for delta calculation
            current_adjs: dict[tuple, float] = {
                (r[0], r[1]): r[2]
                for r in db.execute(
                    "SELECT query_type, model_id, adjustment FROM routing_adjustments"
                ).fetchall()
            }
        finally:
            db.close()

        results: list[RoutingAdjustment] = []
        for query_type, model_id, neg, pos, total in rows:
            current = current_adjs.get((query_type, model_id), 1.0)

            if neg >= FEEDBACK_MIN_SAMPLES and neg > pos:
                new_adj = max(ADJUSTMENT_MIN, current - 0.1 * neg)
                results.append(RoutingAdjustment(
                    query_type=query_type, model_id=model_id,
                    adjustment=new_adj, feedback_count=int(total), kind="penalty",
                ))
            elif pos >= FEEDBACK_MIN_SAMPLES and pos > neg * 2:
                new_adj = min(ADJUSTMENT_MAX, current + 0.05 * pos)
                results.append(RoutingAdjustment(
                    query_type=query_type, model_id=model_id,
                    adjustment=new_adj, feedback_count=int(total), kind="bonus",
                ))

        return results

    def apply_adjustments(self, adjustments: list[RoutingAdjustment]) -> None:
        """Upsert routing_adjustments table and update UserProfile.learning_log if delta is significant.

        Args:
            adjustments: List of RoutingAdjustment objects from analyze_recent_feedback().
        """
        db = self._db()
        try:
            for adj in adjustments:
                # Read current value for delta calculation
                existing = db.execute(
                    "SELECT adjustment FROM routing_adjustments WHERE query_type=? AND model_id=?",
                    (adj.query_type, adj.model_id),
                ).fetchone()
                current_adj = existing[0] if existing else 1.0
                delta = abs(adj.adjustment - current_adj)

                db.execute(
                    """INSERT INTO routing_adjustments (query_type, model_id, adjustment, feedback_count, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(query_type, model_id) DO UPDATE SET
                           adjustment=excluded.adjustment,
                           feedback_count=excluded.feedback_count,
                           updated_at=excluded.updated_at""",
                    (adj.query_type, adj.model_id, adj.adjustment, adj.feedback_count, self._now_iso()),
                )

                if delta > 0.1:
                    try:
                        record_preference_signal(
                            category="routing_adjustment",
                            key=f"{adj.query_type}|{adj.model_id}",
                            value={
                                "type": "routing_adjustment",
                                "query_type": adj.query_type,
                                "model_id": adj.model_id,
                                "adjustment": adj.adjustment,
                                "kind": adj.kind,
                                "timestamp": self._now_iso(),
                            },
                            confidence=1.0,
                        )
                    except Exception as exc:
                        logger.warning("Failed to update learning_log: %s", exc)
            db.commit()
        finally:
            db.close()

        # Invalidate adaptive_router score cache
        try:
            from adaptive_router import adaptive_router
            if hasattr(adaptive_router, "invalidate_score_cache"):
                adaptive_router.invalidate_score_cache()
        except Exception:
            pass
```

Also add the import at the top of `feedback_learner.py`:

```python
from user_profile import record_preference_signal
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py -v
# Expected: all feedback learner tests pass
```

### Commit
```
git add src/bridge/feedback_learner.py tests/test_feedback_learner.py
git commit -m "feat(feedback-learner): implement analyze_recent_feedback() and apply_adjustments()"
```

---

## Task 7 — Integration in `adaptive_router.py`

### What
Modify `src/bridge/adaptive_router.py`:
1. Add `invalidate_score_cache()` method (clears cache)
2. Add `_get_routing_adjustment(task_type, model)` — reads `routing_adjustments` table with 5-minute TTL memory cache
3. Modify `get_model_ranking()` to apply the adjustment multiplier to each model's score

### Red — append to `tests/test_feedback_learner.py`

```python
# ---------------------------------------------------------------------------
# Task 7 — AdaptiveRouter uses routing_adjustments
# ---------------------------------------------------------------------------

class TestAdaptiveRouterAdjustment:

    def test_adaptive_router_uses_adjustment(self, db_path, monkeypatch):
        """get_model_ranking() must apply routing_adjustments multiplier."""
        monkeypatch.setenv("ADAPTIVE_ROUTING_ENABLED", "true")
        monkeypatch.setenv("ADAPTIVE_MIN_SAMPLES", "1")
        monkeypatch.setenv("RAG_STATE_DIR", str(db_path))
        monkeypatch.setenv("STATE_DIR", str(db_path))

        # Pre-seed a penalty for (code, gpt-4o-mini)
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(str(db_path / "feedback.db"))
        db.execute(
            "INSERT INTO routing_adjustments (query_type, model_id, adjustment, feedback_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)", ("code", "gpt-4o-mini", 0.7, 3, now)
        )
        db.commit()
        db.close()

        import importlib
        import adaptive_router as ar_module
        importlib.reload(ar_module)
        router = ar_module.AdaptiveRouter()

        # Record a high quality signal for gpt-4o-mini on code
        router.record_quality("code", "gpt-4o-mini", 0.9)
        router.record_quality("code", "gpt-4o", 0.7)

        ranking = router.get_model_ranking("code", ["gpt-4o-mini", "gpt-4o"])
        # gpt-4o-mini has base 0.9 * 0.7 = 0.63; gpt-4o has 0.7 * 1.0 = 0.70
        # gpt-4o should rank first despite lower raw score
        assert ranking[0] == "gpt-4o"

    def test_adaptive_router_no_adjustment_defaults_to_1(self, db_path, monkeypatch):
        """When no routing_adjustments row exists, multiplier defaults to 1.0."""
        monkeypatch.setenv("ADAPTIVE_ROUTING_ENABLED", "true")
        monkeypatch.setenv("ADAPTIVE_MIN_SAMPLES", "1")
        monkeypatch.setenv("RAG_STATE_DIR", str(db_path))
        monkeypatch.setenv("STATE_DIR", str(db_path))

        import importlib
        import adaptive_router as ar_module
        importlib.reload(ar_module)
        router = ar_module.AdaptiveRouter()
        router.record_quality("factual", "gpt-4o", 0.9)
        # Should not raise — no adjustment row → multiplier 1.0
        ranking = router.get_model_ranking("factual", ["gpt-4o"])
        assert "gpt-4o" in ranking
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py::TestAdaptiveRouterAdjustment -v
# Expected: test_adaptive_router_uses_adjustment fails (adjustment not applied yet)
```

### Green — implement

- [ ] Modify `src/bridge/adaptive_router.py`, adding after the `PREMIUM_ONLY_TASKS` constant:

```python
import sqlite3 as _sqlite3
import pathlib as _pathlib
import time as _time

_ADJUSTMENT_CACHE: dict[str, tuple[float, float]] = {}  # key → (adjustment, cache_time)
_ADJUSTMENT_CACHE_TTL = 300.0  # 5 minutes
_FEEDBACK_DB_PATH = _pathlib.Path(os.getenv("RAG_STATE_DIR", os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))) / "feedback.db"
```

- [ ] Add these methods to `AdaptiveRouter`:

```python
    def invalidate_score_cache(self) -> None:
        """Clear the routing adjustment cache (called after apply_adjustments())."""
        _ADJUSTMENT_CACHE.clear()
        logger.debug("Routing adjustment cache invalidated")

    def _get_routing_adjustment(self, task_type: str, model: str) -> float:
        """Read routing adjustment multiplier from SQLite, with 5-minute TTL cache.

        Returns 1.0 (neutral) if no adjustment row exists or on any error.
        """
        cache_key = f"{task_type}|{model}"
        cached = _ADJUSTMENT_CACHE.get(cache_key)
        if cached and (_time.time() - cached[1]) < _ADJUSTMENT_CACHE_TTL:
            return cached[0]

        try:
            db = _sqlite3.connect(str(_FEEDBACK_DB_PATH))
            row = db.execute(
                "SELECT adjustment FROM routing_adjustments WHERE query_type=? AND model_id=?",
                (task_type, model),
            ).fetchone()
            db.close()
            adjustment = float(row[0]) if row else 1.0
        except Exception:
            adjustment = 1.0

        _ADJUSTMENT_CACHE[cache_key] = (adjustment, _time.time())
        return adjustment
```

- [ ] Modify `get_model_ranking()` — replace the scoring block inside the `with self._lock:` loop:

Before (lines ~113–122 in the original file):
```python
                if entry and entry["samples"] >= MIN_SAMPLES:
                    score = entry["avg_score"]
                    # Apply local model bonus under budget pressure
                    if budget_pressure > 0.5 and _is_local_model(model):
                        score += 0.2 * budget_pressure
                    scored.append((model, score))
                else:
                    unscored.append(model)
```

After:
```python
                if entry and entry["samples"] >= MIN_SAMPLES:
                    score = entry["avg_score"]
                    # Apply local model bonus under budget pressure
                    if budget_pressure > 0.5 and _is_local_model(model):
                        score += 0.2 * budget_pressure
                    # Apply feedback-loop routing adjustment
                    score = score * self._get_routing_adjustment(task_type, model)
                    scored.append((model, score))
                else:
                    unscored.append(model)
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_feedback_learner.py::TestAdaptiveRouterAdjustment -v
# Expected: both tests pass
```

Full test suite check:
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_decay.py tests/test_feedback_learner.py -v
```

### Commit
```
git add src/bridge/adaptive_router.py tests/test_feedback_learner.py
git commit -m "feat(adaptive-router): apply routing_adjustments multiplier in get_model_ranking()"
```

---

## Task 8 — Weekly cron job in `scheduler_registry.py`

### What
Add `Memory Decay Scan` to `SYSTEM_JOBS` in `scheduler_registry.py` with:
- cron: `0 3 * * 1` (Mondays at 3 AM)
- section: `memory_decay_scan` (custom section handled by the job executor)
- Only registered when `MEMORY_DECAY_ENABLED=true`

### Red — write the test first

Create `tests/test_scheduler_registry_decay.py`:

```python
"""Tests for Memory Decay Scan job registration in scheduler_registry."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


class TestMemoryDecayJobRegistration:

    def _make_registry(self, monkeypatch, enabled="true"):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", enabled)
        mgr = MagicMock()
        mgr.list_jobs.return_value = []
        import importlib
        import scheduler_registry
        importlib.reload(scheduler_registry)
        return scheduler_registry.JobRegistry(mgr), mgr

    def test_decay_job_registered_when_enabled(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="true")
        registry.seed()
        created_names = [c.kwargs.get("name") or c.args[0] if c.args else c.kwargs.get("name")
                         for c in mgr.create_job.call_args_list]
        # flatten — create_job is called with keyword args
        all_names = []
        for c in mgr.create_job.call_args_list:
            all_names.append(c.kwargs.get("name", ""))
        assert "Memory Decay Scan" in all_names

    def test_decay_job_cron_is_weekly_monday(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="true")
        registry.seed()
        decay_calls = [
            c for c in mgr.create_job.call_args_list
            if c.kwargs.get("name") == "Memory Decay Scan"
        ]
        assert len(decay_calls) == 1
        assert decay_calls[0].kwargs["cron"] == "0 3 * * 1"

    def test_decay_job_not_registered_when_disabled(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="false")
        registry.seed()
        all_names = [c.kwargs.get("name", "") for c in mgr.create_job.call_args_list]
        assert "Memory Decay Scan" not in all_names

    def test_seed_is_idempotent_when_system_jobs_exist(self, monkeypatch):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", "true")
        mgr = MagicMock()
        mgr.list_jobs.return_value = [{"system": 1, "name": "Briefing matinal"}]
        import importlib
        import scheduler_registry
        importlib.reload(scheduler_registry)
        registry = scheduler_registry.JobRegistry(mgr)
        registry.seed()
        mgr.create_job.assert_not_called()
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_scheduler_registry_decay.py -v
# Expected: test_decay_job_registered_when_enabled fails
```

### Green — implement

- [ ] Modify `src/bridge/scheduler_registry.py` — add the `_MEMORY_DECAY_JOB` constant and conditional registration:

After `_BACKUP_JOB`, add:

```python
_MEMORY_DECAY_JOB = {
    "name": "Memory Decay Scan",
    "cron": "0 3 * * 1",
    "sections": ["memory_decay_scan"],
    "channels": [],
    "prompt": "",
    "timeout_s": 600,
}
```

In `JobRegistry.seed()`, after the backup block, add:

```python
        # Register the memory decay scan job only when MEMORY_DECAY_ENABLED=true
        if _env_bool("MEMORY_DECAY_ENABLED", False):
            jobs.append(_MEMORY_DECAY_JOB)
            logger.info("MEMORY_DECAY_ENABLED=true — registering Memory Decay Scan job")
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_scheduler_registry_decay.py -v
# Expected: all 4 tests pass
```

### Commit
```
git add src/bridge/scheduler_registry.py tests/test_scheduler_registry_decay.py
git commit -m "feat(scheduler): register Memory Decay Scan weekly job when MEMORY_DECAY_ENABLED=true"
```

---

## Task 9 — `memory_api.py` — REST endpoints

### What
Create `src/bridge/memory_api.py` with two FastAPI routers:

**`/api/memory` prefix:**
- `POST /decay/run` — trigger `run_decay_scan()` manually; `503` if disabled
- `GET /decay/log` — paginated `memory_decay_log` (`?limit&offset&collection`)
- `GET /decay/preview` — read points from Qdrant + compute scores without applying changes
- `POST /forget` — call `forget(collection, point_id)`, `404` if point missing
- `GET /health` — stats per collection (count, avg score, below-threshold count)

**`/api/feedback` prefix:**
- `POST /` — call `record_feedback()`; `404` if `query_id` unknown; `201` on success
- `GET /summary` — feedback stats + active adjustments
- `GET /adjustments` — list `routing_adjustments` table
- `DELETE /adjustments/{query_type}/{model_id}` — reset adjustment to 1.0

### Red — create `tests/test_memory_api.py`

```python
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
    import memory_api
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
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        app = FastAPI()
        import importlib
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
```

Run (red):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_api.py -v
# Expected: ModuleNotFoundError for memory_api
```

### Green — implement

- [ ] Create `src/bridge/memory_api.py`:

```python
"""memory_api — REST endpoints for memory decay management and feedback loop.

Provides two FastAPI routers:
  - memory_router: /api/memory/... (decay scan, log, preview, forget, health)
  - feedback_router: /api/feedback/... (record, summary, adjustments)
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.memory_api")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")))

memory_router = APIRouter(prefix="/api/memory", tags=["memory-decay"])
feedback_router = APIRouter(prefix="/api/feedback", tags=["feedback"])

_qdrant_client: Any = None
_decay_manager: Any = None
_feedback_learner: Any = None


def init_memory_api(qdrant_client: Any) -> None:
    """Initialise the API with a Qdrant client instance. Call once at app startup."""
    global _qdrant_client, _decay_manager, _feedback_learner
    _qdrant_client = qdrant_client
    from memory_decay import MemoryDecayManager, MEMORY_DECAY_ENABLED as _decay_enabled
    from feedback_learner import FeedbackLearner
    _decay_manager = MemoryDecayManager(qdrant_client=qdrant_client)
    _feedback_learner = FeedbackLearner()


def _db() -> sqlite3.Connection:
    return sqlite3.connect(str(STATE_DIR / "feedback.db"))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ForgetRequest(BaseModel):
    collection: str
    point_id: str


class FeedbackRequest(BaseModel):
    query_id: str
    query_type: str
    model_used: str
    was_helpful: bool
    correction_text: str | None = None


# ---------------------------------------------------------------------------
# /api/memory routes
# ---------------------------------------------------------------------------

@memory_router.post("/decay/run")
def decay_run():
    """Trigger a manual decay scan. Returns 503 if MEMORY_DECAY_ENABLED=false."""
    from memory_decay import MEMORY_DECAY_ENABLED
    if not MEMORY_DECAY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Memory decay is disabled. Set MEMORY_DECAY_ENABLED=true to enable.",
        )
    result = _decay_manager.run_decay_scan()
    return result


@memory_router.get("/decay/log")
def decay_log(limit: int = 50, offset: int = 0, collection: str | None = None):
    """Paginated history of memory_decay_log."""
    db = _db()
    try:
        if collection:
            rows = db.execute(
                "SELECT id, collection, point_id, old_score, new_score, reason, created_at "
                "FROM memory_decay_log WHERE collection=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (collection, limit, offset),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, collection, point_id, old_score, new_score, reason, created_at "
                "FROM memory_decay_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    finally:
        db.close()
    return [
        {"id": r[0], "collection": r[1], "point_id": r[2],
         "old_score": r[3], "new_score": r[4], "reason": r[5], "created_at": r[6]}
        for r in rows
    ]


@memory_router.get("/decay/preview")
def decay_preview(collection: str = "memory_personal", limit: int = 20):
    """Preview decay scores without applying changes."""
    from memory_decay import MEMORY_DECAY_ENABLED, MEMORY_DECAY_THRESHOLD
    try:
        points, _ = _qdrant_client.scroll(
            collection_name=collection, limit=limit, with_payload=True
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant error: {exc}") from exc

    preview = []
    for pt in points:
        payload = pt.payload or {}
        confidence = float(payload.get("confidence_score", 1.0))
        last_accessed = payload.get("last_accessed", payload.get("created_at", datetime.now(timezone.utc).isoformat()))
        try:
            from datetime import datetime as _dt
            la = _dt.fromisoformat(last_accessed.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - la).total_seconds() / 86400.0
        except Exception:
            days = 0.0
        new_score = _decay_manager.score_point(confidence, days)
        preview.append({
            "point_id": str(pt.id),
            "current_score": confidence,
            "projected_score": round(new_score, 4),
            "days_since_access": round(days, 1),
            "would_be_deleted": new_score < MEMORY_DECAY_THRESHOLD,
        })
    preview.sort(key=lambda x: x["projected_score"])
    return preview


@memory_router.post("/forget")
def forget(req: ForgetRequest):
    """Explicitly delete a memory point (with audit log)."""
    result = _decay_manager.forget(req.collection, req.point_id)
    return result


@memory_router.get("/health")
def memory_health():
    """Memory health stats: point counts, avg confidence, below-threshold count."""
    from memory_decay import MEMORY_DECAY_COLLECTIONS, MEMORY_DECAY_THRESHOLD, MEMORY_DECAY_ENABLED
    stats = {"enabled": MEMORY_DECAY_ENABLED, "collections": []}
    for coll in MEMORY_DECAY_COLLECTIONS:
        try:
            points, _ = _qdrant_client.scroll(
                collection_name=coll, limit=1000, with_payload=True
            )
            scores = [float((p.payload or {}).get("confidence_score", 1.0)) for p in points]
            below = sum(1 for s in scores if s < MEMORY_DECAY_THRESHOLD)
            stats["collections"].append({
                "collection": coll,
                "total_points": len(scores),
                "avg_confidence": round(sum(scores) / len(scores), 4) if scores else 1.0,
                "below_threshold": below,
            })
        except Exception as exc:
            stats["collections"].append({"collection": coll, "error": str(exc)})
    return stats


# ---------------------------------------------------------------------------
# /api/feedback routes
# ---------------------------------------------------------------------------

@feedback_router.post("/", status_code=201)
def post_feedback(req: FeedbackRequest):
    """Submit feedback on a response."""
    try:
        _feedback_learner.record_feedback(
            query_id=req.query_id,
            query_type=req.query_type,
            model_used=req.model_used,
            was_helpful=req.was_helpful,
            correction_text=req.correction_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"recorded": True, "query_id": req.query_id}


@feedback_router.get("/summary")
def feedback_summary(window_days: int = 7):
    """Feedback statistics and active routing adjustments."""
    db = _db()
    try:
        total = db.execute("SELECT COUNT(*) FROM feedback WHERE was_helpful IS NOT NULL").fetchone()[0]
        pos = db.execute("SELECT COUNT(*) FROM feedback WHERE was_helpful=1").fetchone()[0]
        neg = db.execute("SELECT COUNT(*) FROM feedback WHERE was_helpful=0").fetchone()[0]
        adjustments = db.execute(
            "SELECT query_type, model_id, adjustment, feedback_count, updated_at "
            "FROM routing_adjustments ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        db.close()
    return {
        "total_feedbacks": total,
        "positive": pos,
        "negative": neg,
        "window_days": window_days,
        "active_adjustments": [
            {"query_type": r[0], "model_id": r[1], "adjustment": r[2],
             "feedback_count": r[3], "updated_at": r[4]}
            for r in adjustments
        ],
    }


@feedback_router.get("/adjustments")
def get_adjustments():
    """List all active routing adjustments."""
    db = _db()
    try:
        rows = db.execute(
            "SELECT query_type, model_id, adjustment, feedback_count, updated_at "
            "FROM routing_adjustments ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        db.close()
    return [
        {"query_type": r[0], "model_id": r[1], "adjustment": r[2],
         "feedback_count": r[3], "updated_at": r[4]}
        for r in rows
    ]


@feedback_router.delete("/adjustments/{query_type}/{model_id}")
def reset_adjustment(query_type: str, model_id: str):
    """Reset a routing adjustment to 1.0 (neutral)."""
    db = _db()
    try:
        db.execute(
            "DELETE FROM routing_adjustments WHERE query_type=? AND model_id=?",
            (query_type, model_id),
        )
        db.commit()
    finally:
        db.close()
    # Invalidate cache
    try:
        from adaptive_router import adaptive_router
        if hasattr(adaptive_router, "invalidate_score_cache"):
            adaptive_router.invalidate_score_cache()
    except Exception:
        pass
    return {"reset": True, "query_type": query_type, "model_id": model_id}
```

Run (green):
```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_memory_api.py -v
# Expected: all 11 tests pass
```

### Commit
```
git add src/bridge/memory_api.py tests/test_memory_api.py
git commit -m "feat(memory-api): add REST endpoints for decay management and feedback loop"
```

---

## Task 10 — Mount routers in `app.py`

### What
Modify `src/bridge/app.py` to:
1. Import `memory_api` and call `init_memory_api(qdrant_client=client)` at startup
2. Mount `memory_router` and `feedback_router` on the FastAPI app
3. Add `confirm_access()` calls after retrieval in the `/chat` endpoint

### Red

No isolated test needed for mounting (covered by integration). Verify manually:
```bash
cd /opt/nanobot-stack/rag-bridge && python -c "import app; print('OK')"
```

### Green — implement

- [ ] In `src/bridge/app.py`, add the import near the other module imports (after the scheduler imports block, around line 52):

```python
from memory_api import memory_router, feedback_router, init_memory_api
```

- [ ] Find the FastAPI app creation and router mounting section (around line 517 — `# v8: Mount extension endpoints`). Add after the existing `app.include_router(scheduler_router)` line:

```python
# Sub-projet H: memory decay + feedback loop
init_memory_api(qdrant_client=client)
app.include_router(memory_router)
app.include_router(feedback_router)
```

- [ ] In the `/chat` endpoint, find the section where `retrieved_points` are assembled (after `apply_feedback_boosts`). Add `confirm_access` calls:

```python
# Sub-projet H: reinforce memory for retrieved points
try:
    from memory_decay import MemoryDecayManager, MEMORY_DECAY_ENABLED
    if MEMORY_DECAY_ENABLED and retrieved_points:
        _decay_mgr = MemoryDecayManager(qdrant_client=client)
        for rp in retrieved_points:
            _decay_mgr.confirm_access(rp.get("collection", "memory_personal"), rp["id"])
except Exception as _decay_exc:
    logger.debug("confirm_access failed (non-critical): %s", _decay_exc)
```

Verify:
```bash
cd /opt/nanobot-stack/rag-bridge && python -c "import app; print('app imports OK')"
```

### Commit
```
git add src/bridge/app.py
git commit -m "feat(app): mount memory/feedback routers and call confirm_access() after retrieval"
```

---

## Task 11 — Full test suite validation

### What
Run all new test files together and ensure the complete existing test suite is unbroken.

### Run all Sub-projet H tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest \
  tests/test_migration_016.py \
  tests/test_memory_decay.py \
  tests/test_feedback_learner.py \
  tests/test_memory_api.py \
  tests/test_scheduler_registry_decay.py \
  -v --tb=short
```

Expected: all tests green.

### Run full existing suite to check no regressions

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/ -v --tb=short
```

Check specifically:
- `tests/test_scheduler_api.py` — scheduler unchanged
- `tests/test_backup_manager.py` — backup unchanged
- `tests/test_broadcast_notifier.py` — notifier unchanged

### Fix any regressions before proceeding

If `adaptive_router` tests break due to the new import of `_sqlite3`/`_pathlib`/`_time` at module level:

Verify the module-level import does not break existing tests that do not set `RAG_STATE_DIR`:
```python
# In adaptive_router.py — the _FEEDBACK_DB_PATH resolution must be lazy-safe
# Use a function instead of module-level constant if tests fail:
def _feedback_db_path() -> pathlib.Path:
    return pathlib.Path(
        os.getenv("RAG_STATE_DIR", os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
    ) / "feedback.db"
```

And update `_get_routing_adjustment` to call `_feedback_db_path()` instead of `_FEEDBACK_DB_PATH`.

### Commit
```
git add tests/
git commit -m "test(sub-project-h): complete TDD test suite for memory decay and feedback loop"
```

---

## Task 12 — Final integration commit

### What
After all tasks pass, create a clean integration commit tying everything together.

### Checklist

- [ ] `migrations/018_memory_decay_feedback.py` — VERSION=16, check(), migrate()
- [ ] `src/bridge/memory_decay.py` — MemoryDecayManager complete
- [ ] `src/bridge/feedback_learner.py` — FeedbackLearner complete
- [ ] `src/bridge/adaptive_router.py` — routing adjustments applied
- [ ] `src/bridge/scheduler_registry.py` — Memory Decay Scan job registered
- [ ] `src/bridge/memory_api.py` — all REST endpoints
- [ ] `src/bridge/app.py` — routers mounted + confirm_access() calls
- [ ] `tests/test_migration_016.py` — 8 tests
- [ ] `tests/test_memory_decay.py` — 17+ tests (TestScorePointMath + TestRunDecayScan + TestConfirmAndForget)
- [ ] `tests/test_feedback_learner.py` — 15+ tests (TestRecordFeedback + TestAnalyzeAndApply + TestAdaptiveRouterAdjustment)
- [ ] `tests/test_memory_api.py` — 11+ tests
- [ ] `tests/test_scheduler_registry_decay.py` — 4 tests

### Final run

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

### Commit
```
git add -A
git commit -m "feat(sub-project-h): complete Memory Decay & Feedback Loop implementation

- MemoryDecayManager: exponential decay, safety guard, confirm_access, forget
- FeedbackLearner: record feedback, analyze rolling window, apply routing adjustments
- AdaptiveRouter: apply routing_adjustments multiplier in get_model_ranking()
- Migration 016: memory_decay_log + routing_adjustments + feedback extension
- Weekly cron job registered via scheduler_registry when MEMORY_DECAY_ENABLED=true
- REST API: /api/memory/* and /api/feedback/* endpoints
- All features opt-in via MEMORY_DECAY_ENABLED and FEEDBACK_LEARNING_ENABLED"
```

---

## Environment Variables Summary

| Variable | Default | Feature |
|----------|---------|---------|
| `MEMORY_DECAY_ENABLED` | `false` | Enable decay system |
| `MEMORY_DECAY_LAMBDA` | `0.01` | Decay rate (half-life ≈ 70 days) |
| `MEMORY_DECAY_THRESHOLD` | `0.1` | Delete below this score |
| `MEMORY_DECAY_COLLECTIONS` | `memory_personal,conversation_summaries` | Target collections |
| `FEEDBACK_LEARNING_ENABLED` | `false` | Enable routing adjustments |
| `FEEDBACK_WINDOW_DAYS` | `7` | Rolling analysis window |
| `FEEDBACK_MIN_SAMPLES` | `3` | Minimum negatives before penalty |

## Files Created / Modified

| File | Action |
|------|--------|
| `migrations/018_memory_decay_feedback.py` | Create |
| `src/bridge/memory_decay.py` | Create |
| `src/bridge/feedback_learner.py` | Create |
| `src/bridge/memory_api.py` | Create |
| `src/bridge/adaptive_router.py` | Modify — add `_get_routing_adjustment()`, `invalidate_score_cache()`, apply multiplier in `get_model_ranking()` |
| `src/bridge/scheduler_registry.py` | Modify — add `_MEMORY_DECAY_JOB` + conditional registration |
| `src/bridge/app.py` | Modify — mount routers, call `confirm_access()` after retrieval |
| `tests/test_migration_016.py` | Create |
| `tests/test_memory_decay.py` | Create |
| `tests/test_feedback_learner.py` | Create |
| `tests/test_memory_api.py` | Create |
| `tests/test_scheduler_registry_decay.py` | Create |
