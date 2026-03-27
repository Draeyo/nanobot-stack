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
    global _qdrant_client, _decay_manager, _feedback_learner  # pylint: disable=global-statement
    _qdrant_client = qdrant_client
    from memory_decay import MemoryDecayManager
    from feedback_learner import FeedbackLearner
    _decay_manager = MemoryDecayManager(qdrant_client=qdrant_client)
    _feedback_learner = FeedbackLearner()


def _db() -> sqlite3.Connection:
    return sqlite3.connect(str(STATE_DIR / "feedback.db"))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ForgetRequest(BaseModel):
    """Request body for explicit memory deletion."""
    collection: str
    point_id: str


class FeedbackRequest(BaseModel):
    """Request body for submitting feedback on a response."""
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
    from memory_decay import MEMORY_DECAY_THRESHOLD
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
            la = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
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
    stats: dict[str, Any] = {"enabled": MEMORY_DECAY_ENABLED, "collections": []}
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
