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

from user_profile import record_preference_signal

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
    """Represents a computed routing weight adjustment."""
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

    # ------------------------------------------------------------------
    # analyze_recent_feedback
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # apply_adjustments
    # ------------------------------------------------------------------

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
