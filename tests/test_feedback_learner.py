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


_FEEDBACK_COUNTER = 0

def _insert_feedback_row(db_path, chunk_id, query_type=None, model_used=None,
                          was_helpful=None, days_ago=0):
    global _FEEDBACK_COUNTER  # pylint: disable=global-statement
    _FEEDBACK_COUNTER += 1
    now = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    signal = "positive" if was_helpful else "negative"
    db = sqlite3.connect(str(db_path / "feedback.db"))
    db.execute(
        "INSERT INTO feedback (chunk_id, collection, query, signal, created_at, query_type, model_used, was_helpful) "
        "VALUES (?, 'col', ?, ?, ?, ?, ?, ?)",
        (chunk_id, f"q-{_FEEDBACK_COUNTER}", signal, now, query_type, model_used, was_helpful),
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Task 5 — record_feedback()
# ---------------------------------------------------------------------------

class TestRecordFeedback:

    def _make_learner(self, db_path):
        import importlib
        import feedback_learner
        importlib.reload(feedback_learner)
        return feedback_learner.FeedbackLearner()

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


# ---------------------------------------------------------------------------
# Task 6 — analyze_recent_feedback() and apply_adjustments()
# ---------------------------------------------------------------------------

class TestAnalyzeAndApply:

    def _make_learner(self, db_path, monkeypatch, learning_enabled="false"):
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", learning_enabled)
        monkeypatch.setenv("FEEDBACK_MIN_SAMPLES", "3")
        monkeypatch.setenv("FEEDBACK_WINDOW_DAYS", "7")
        import importlib
        import feedback_learner
        importlib.reload(feedback_learner)
        return feedback_learner.FeedbackLearner()

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
        import feedback_learner
        adj = feedback_learner.RoutingAdjustment("code", "gpt-4o-mini", 0.8, 3, "penalty")
        with patch("feedback_learner.record_preference_signal"):
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
        import feedback_learner
        adj = feedback_learner.RoutingAdjustment("analysis", "gpt-4o", 1.2, 5, "bonus")
        with patch("feedback_learner.record_preference_signal"):
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
        import feedback_learner
        adj = feedback_learner.RoutingAdjustment("code", "gpt-4o-mini", 0.7, 3, "penalty")
        # Start from 1.0 default -> delta = 0.3 > 0.1
        with patch("feedback_learner.record_preference_signal") as mock_signal:
            learner.apply_adjustments([adj])
            mock_signal.assert_called_once()

    def test_learning_log_not_updated_small_delta(self, db_path, monkeypatch):
        learner = self._make_learner(db_path, monkeypatch)
        import feedback_learner
        # First upsert to set baseline to 0.95
        db = sqlite3.connect(str(db_path / "feedback.db"))
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO routing_adjustments (query_type, model_id, adjustment, feedback_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)", ("code", "gpt-4o", 0.95, 2, now)
        )
        db.commit()
        db.close()
        adj = feedback_learner.RoutingAdjustment("code", "gpt-4o", 0.97, 3, "bonus")  # delta = 0.02 < 0.1
        with patch("feedback_learner.record_preference_signal") as mock_signal:
            learner.apply_adjustments([adj])
            mock_signal.assert_not_called()

    def test_feedback_learning_disabled_no_adjustment(self, db_path, monkeypatch):
        monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
        _insert_feedback_row(db_path, "qid-nolearn")
        import importlib
        import feedback_learner
        importlib.reload(feedback_learner)
        learner = feedback_learner.FeedbackLearner()
        learner.record_feedback("qid-nolearn", "code", "gpt-4o-mini", was_helpful=0)
        db = sqlite3.connect(str(db_path / "feedback.db"))
        adj_count = db.execute("SELECT COUNT(*) FROM routing_adjustments").fetchone()[0]
        db.close()
        assert adj_count == 0


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
        # Should not raise — no adjustment row -> multiplier 1.0
        ranking = router.get_model_ranking("factual", ["gpt-4o"])
        assert "gpt-4o" in ranking
