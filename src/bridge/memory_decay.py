"""MemoryDecayManager — exponential confidence decay for Qdrant permanent collections.

Opt-in via MEMORY_DECAY_ENABLED=true. When disabled, all methods are no-ops.
Also provides the legacy helper functions (compute_decay, apply_decay_to_results)
for backwards compatibility with existing search pipeline integration.
"""
from __future__ import annotations

import logging
import math
import os
import pathlib
import sqlite3
import time
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

# Legacy compat env vars
HALF_LIFE_DAYS = float(os.getenv("MEMORY_HALF_LIFE_DAYS", "30"))
ACCESS_BOOST = float(os.getenv("MEMORY_ACCESS_BOOST", "0.1"))
MIN_SCORE_MULTIPLIER = float(os.getenv("MEMORY_MIN_SCORE", "0.1"))


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
        """Apply exponential decay formula: confidence * e^(-lambda * days).

        Args:
            confidence: Current confidence score (0.0-1.0).
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

    # ------------------------------------------------------------------
    # run_decay_scan
    # ------------------------------------------------------------------

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
        result_collections: list[dict[str, Any]] = []

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

    # ------------------------------------------------------------------
    # confirm_access and forget
    # ------------------------------------------------------------------

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

        db = self._db()
        try:
            self._log_decay(db, collection, str(point_id), old_score, 1.0, "confirm")
            db.commit()
        finally:
            db.close()

        try:
            self._qdrant.set_payload(
                collection_name=collection,
                payload={"confidence_score": 1.0, "last_accessed": _now_iso()},
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


# ---------------------------------------------------------------------------
# Legacy helper functions (backwards compatibility with existing search pipeline)
# ---------------------------------------------------------------------------

DECAY_ENABLED = os.getenv("MEMORY_DECAY_ENABLED", "true").lower() == "true"


def compute_decay(
    created_at: str | float,
    last_accessed: str | float | None = None,
    access_count: int = 0,
    importance: str = "medium",
) -> float:
    """Compute a decay multiplier for a memory.

    Args:
        created_at: ISO timestamp or Unix epoch of creation.
        last_accessed: ISO timestamp or epoch of last access (defaults to created_at).
        access_count: Number of times this memory was retrieved.
        importance: 'high', 'medium', or 'low'.

    Returns:
        Multiplier 0.0-1.0+ to apply to the memory's relevance score.
    """
    if not DECAY_ENABLED:
        return 1.0

    now = time.time()
    created_ts = _to_epoch(created_at)
    accessed_ts = _to_epoch(last_accessed) if last_accessed else created_ts

    # Use whichever is more recent
    reference_ts = max(created_ts, accessed_ts)
    age_days = max(0, (now - reference_ts) / 86400)

    # Exponential decay: score = 0.5^(age / half_life)
    decay = math.pow(0.5, age_days / HALF_LIFE_DAYS)

    # Access frequency boost (logarithmic, caps at ~0.3 extra)
    access_boost = min(0.3, ACCESS_BOOST * math.log1p(access_count))

    # Importance modifier
    importance_mult = {"high": 1.3, "medium": 1.0, "low": 0.7}.get(importance, 1.0)

    multiplier = (decay + access_boost) * importance_mult
    return max(MIN_SCORE_MULTIPLIER, min(1.5, multiplier))


def apply_decay_to_results(
    results: list[dict[str, Any]],
    score_key: str = "score",
) -> list[dict[str, Any]]:
    """Apply decay multipliers to search results and re-sort.

    Each result should have metadata with 'created_at', optionally
    'last_accessed', 'access_count', 'importance'.
    """
    if not DECAY_ENABLED:
        return results

    for r in results:
        meta = r.get("metadata", r.get("payload", {}))
        created = meta.get("created_at", meta.get("ingested_at", ""))
        if not created:
            continue
        multiplier = compute_decay(
            created_at=created,
            last_accessed=meta.get("last_accessed"),
            access_count=meta.get("access_count", 0),
            importance=meta.get("importance", "medium"),
        )
        original_score = r.get(score_key, 0.0)
        r["decay_multiplier"] = round(multiplier, 3)
        r["original_score"] = original_score
        r[score_key] = round(original_score * multiplier, 4)

    results.sort(key=lambda x: x.get(score_key, 0), reverse=True)
    return results


def _to_epoch(ts: str | float | None) -> float:
    """Convert an ISO timestamp or epoch float to Unix epoch."""
    if ts is None:
        return time.time()
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return time.time()
