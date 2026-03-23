"""Memory decay — time and access-based scoring for memories.

Older and less-accessed memories decay exponentially so that recent,
frequently-used knowledge naturally bubbles up in search results.
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.memory_decay")

DECAY_ENABLED = os.getenv("MEMORY_DECAY_ENABLED", "true").lower() == "true"
HALF_LIFE_DAYS = float(os.getenv("MEMORY_HALF_LIFE_DAYS", "30"))
ACCESS_BOOST = float(os.getenv("MEMORY_ACCESS_BOOST", "0.1"))
MIN_SCORE_MULTIPLIER = float(os.getenv("MEMORY_MIN_SCORE", "0.1"))


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
