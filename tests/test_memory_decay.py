"""Tests for MemoryDecayManager."""
from __future__ import annotations
import math
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
        import importlib
        import memory_decay
        importlib.reload(memory_decay)
        return memory_decay.MemoryDecayManager(qdrant_client=mock_qdrant)

    def test_decay_formula_math(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # confidence=1.0, days=70, lambda=0.01 -> 1.0 * e^(-0.01*70) ~ 0.4966
        result = mgr.score_point(1.0, 70.0)
        expected = 1.0 * math.exp(-0.01 * 70.0)
        assert abs(result - expected) < 0.0001

    def test_decay_with_zero_days(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # 0 days elapsed -> multiplier = e^0 = 1.0 -> score unchanged
        result = mgr.score_point(0.8, 0.0)
        assert abs(result - 0.8) < 0.0001

    def test_decay_half_life(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # lambda=0.01, half-life = ln(2)/0.01 ~ 69.3 days
        half_life = math.log(2) / 0.01
        result = mgr.score_point(1.0, half_life)
        assert abs(result - 0.5) < 0.01

    def test_score_clamped_to_zero_minimum(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # Extremely high days -> score approaches 0, never negative
        result = mgr.score_point(1.0, 10000.0)
        assert result >= 0.0

    def test_score_clamped_to_one_maximum(self, monkeypatch, tmp_path):
        mgr = self._make_manager(monkeypatch, tmp_path)
        # confidence already 1.0, days=0 -> should not exceed 1.0
        result = mgr.score_point(1.0, 0.0)
        assert result <= 1.0

    def test_disabled_score_point_still_works(self, monkeypatch, tmp_path):
        # score_point is pure math — it works regardless of ENABLED flag
        mgr = self._make_manager(monkeypatch, tmp_path, enabled="false")
        result = mgr.score_point(0.6, 30.0)
        expected = 0.6 * math.exp(-0.01 * 30.0)
        assert abs(result - expected) < 0.0001


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
        import importlib
        import memory_decay
        importlib.reload(memory_decay)
        return memory_decay.MemoryDecayManager(qdrant_client=mock_qdrant), mock_qdrant

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
        # Point with confidence 0.15 and 200 days old
        # -> score_point(0.15, 200) = 0.15 * e^(-0.01*200) ~ 0.020 < 0.1 -> should be deleted
        low_pt = self._make_point("pt-low", confidence=0.15, days_old=200)
        # Add 8 healthy points so bulk_delete_guard (>20%) does not trigger: 1/9 ~ 11%
        healthy_pts = [self._make_point(f"pt-ok-{i}", confidence=0.9, days_old=1) for i in range(8)]
        mock_qdrant.scroll.return_value = ([low_pt] + healthy_pts, None)
        mgr.run_decay_scan("memory_personal")
        mock_qdrant.delete.assert_called_once()

    def test_threshold_no_delete_above(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # Point with confidence 0.9, 1 day old -> score > 0.1 -> no delete
        high_pt = self._make_point("pt-high", confidence=0.9, days_old=1)
        mock_qdrant.scroll.return_value = ([high_pt], None)
        mgr.run_decay_scan("memory_personal")
        mock_qdrant.delete.assert_not_called()

    def test_audit_log_created_before_delete(self, monkeypatch, tmp_path):
        mgr, mock_qdrant = self._make_manager(monkeypatch, tmp_path)
        # confidence=0.15, 200 days -> decays well below threshold
        low_pt = self._make_point("pt-audit", confidence=0.15, days_old=200)
        # Add healthy points to avoid bulk_delete_guard
        healthy_pts = [self._make_point(f"pt-ok-{i}", confidence=0.9, days_old=1) for i in range(8)]
        mock_qdrant.scroll.return_value = ([low_pt] + healthy_pts, None)

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
        # 4 points all below threshold (100% -> exceeds 20% guard)
        low_pts = [self._make_point(f"pt-{i}", confidence=0.15, days_old=200) for i in range(4)]
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
        import importlib
        import memory_decay
        importlib.reload(memory_decay)
        return memory_decay.MemoryDecayManager(qdrant_client=mock_qdrant), mock_qdrant

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
