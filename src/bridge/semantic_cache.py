"""Semantic L2 cache for LLM responses using Qdrant vector similarity.

The existing L1 cache (``LLMResponseCache`` in *token_optimizer.py*) is an
in-memory exact-match dict.  This module provides an L2 layer: when L1
misses, check here before calling the LLM.  Responses are stored as
vectors in a Qdrant collection and retrieved via cosine similarity.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.semantic-cache")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SEMANTIC_CACHE_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "false").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
SEMANTIC_CACHE_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "86400"))  # 24 hours
SEMANTIC_CACHE_MAX_SIZE = int(os.getenv("SEMANTIC_CACHE_MAX_SIZE", "1000"))
SEMANTIC_CACHE_COLLECTION = "semantic_cache"


# ---------------------------------------------------------------------------
# SemanticCache
# ---------------------------------------------------------------------------

class SemanticCache:
    """Vector-similarity-based LLM response cache using Qdrant."""

    def __init__(self, qdrant_client: Any = None, embed_fn: Callable[[str], list[float]] | None = None):
        """
        Args:
            qdrant_client: ``qdrant_client.QdrantClient`` instance.
            embed_fn: callable(text) -> list[float] for embedding.
        """
        self._client = qdrant_client
        self._embed_fn = embed_fn
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._collection_ready = False
        self._vector_size: int | None = None

    # -- internal helpers ---------------------------------------------------

    def _ensure_collection(self) -> None:
        """Create the semantic_cache collection if it doesn't exist."""
        if self._collection_ready:
            return

        with self._lock:
            if self._collection_ready:
                return
            try:
                from qdrant_client import models

                # Determine vector size from a probe embedding if unknown.
                if self._vector_size is None:
                    probe = self._embed_fn("probe")
                    self._vector_size = len(probe)

                collections = [c.name for c in self._client.get_collections().collections]
                if SEMANTIC_CACHE_COLLECTION not in collections:
                    self._client.create_collection(
                        collection_name=SEMANTIC_CACHE_COLLECTION,
                        vectors_config=models.VectorParams(
                            size=self._vector_size,
                            distance=models.Distance.COSINE,
                        ),
                    )
                    logger.info(
                        "Created Qdrant collection '%s' (vector_size=%d)",
                        SEMANTIC_CACHE_COLLECTION,
                        self._vector_size,
                    )

                self._collection_ready = True
            except Exception:
                logger.warning("Failed to ensure Qdrant collection", exc_info=True)

    # -- public API ---------------------------------------------------------

    def get(self, task_type: str, query: str) -> dict | None:
        """Search for a cached response by semantic similarity.

        Returns ``{"text": ..., "task_type": ..., "cached_at": ...}`` on a
        hit, or ``None`` on a miss.  Filters by *task_type* and TTL.  Only
        returns results whose similarity score meets the configured threshold.
        """
        try:
            self._ensure_collection()
            if not self._collection_ready:
                self._misses += 1
                return None

            from qdrant_client import models

            vector = self._embed_fn(query)

            results = self._client.search(
                collection_name=SEMANTIC_CACHE_COLLECTION,
                query_vector=vector,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="task_type",
                            match=models.MatchValue(value=task_type),
                        ),
                    ],
                ),
                limit=1,
                score_threshold=SEMANTIC_CACHE_THRESHOLD,
            )

            if not results:
                self._misses += 1
                return None

            hit = results[0]
            payload = hit.payload or {}

            # TTL check
            cached_at_str = payload.get("cached_at", "")
            if cached_at_str:
                try:
                    cached_at = datetime.fromisoformat(cached_at_str)
                    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
                    if age > SEMANTIC_CACHE_TTL:
                        self._misses += 1
                        # Optionally delete stale entry
                        self._client.delete(
                            collection_name=SEMANTIC_CACHE_COLLECTION,
                            points_selector=models.PointIdsList(points=[hit.id]),
                        )
                        return None
                except (ValueError, TypeError):
                    pass

            self._hits += 1
            logger.debug(
                "Semantic cache hit for task_type=%s (score=%.4f)",
                task_type,
                hit.score,
            )
            return {
                "text": payload.get("response", ""),
                "task_type": payload.get("task_type", task_type),
                "cached_at": cached_at_str,
            }
        except Exception:
            logger.warning("Semantic cache get failed", exc_info=True)
            self._misses += 1
            return None

    def put(self, task_type: str, query: str, response: str) -> None:
        """Store a response in the semantic cache.

        Creates an embedding of *query* and upserts a point into Qdrant with
        payload containing the response and metadata.  Enforces
        ``SEMANTIC_CACHE_MAX_SIZE`` by deleting the oldest entries when the
        limit is exceeded.
        """
        try:
            self._ensure_collection()
            if not self._collection_ready:
                return

            from qdrant_client import models

            vector = self._embed_fn(query)
            point_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()

            self._client.upsert(
                collection_name=SEMANTIC_CACHE_COLLECTION,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "task_type": task_type,
                            "query": query,
                            "response": response,
                            "cached_at": now,
                        },
                    ),
                ],
            )

            # Enforce max size by deleting oldest entries.
            info = self._client.get_collection(SEMANTIC_CACHE_COLLECTION)
            count = info.points_count or 0
            if count > SEMANTIC_CACHE_MAX_SIZE:
                excess = count - SEMANTIC_CACHE_MAX_SIZE
                # Scroll to find the oldest points (sorted by cached_at).
                oldest, _ = self._client.scroll(
                    collection_name=SEMANTIC_CACHE_COLLECTION,
                    limit=excess,
                    order_by=models.OrderBy(key="cached_at", direction=models.Direction.ASC),
                )
                if oldest:
                    ids_to_delete = [p.id for p in oldest]
                    self._client.delete(
                        collection_name=SEMANTIC_CACHE_COLLECTION,
                        points_selector=models.PointIdsList(points=ids_to_delete),
                    )
                    logger.debug("Evicted %d oldest semantic cache entries", len(ids_to_delete))
        except Exception:
            logger.warning("Semantic cache put failed", exc_info=True)

    def invalidate(self, task_type: str | None = None) -> int:
        """Clear cache entries.

        If *task_type* is given only entries of that type are removed.
        Returns the number of entries deleted.
        """
        try:
            self._ensure_collection()
            if not self._collection_ready:
                return 0

            from qdrant_client import models

            if task_type is not None:
                # Count matching entries first.
                count_result = self._client.count(
                    collection_name=SEMANTIC_CACHE_COLLECTION,
                    count_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="task_type",
                                match=models.MatchValue(value=task_type),
                            ),
                        ],
                    ),
                )
                deleted = count_result.count

                self._client.delete(
                    collection_name=SEMANTIC_CACHE_COLLECTION,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="task_type",
                                    match=models.MatchValue(value=task_type),
                                ),
                            ],
                        ),
                    ),
                )
            else:
                info = self._client.get_collection(SEMANTIC_CACHE_COLLECTION)
                deleted = info.points_count or 0
                self._client.delete_collection(SEMANTIC_CACHE_COLLECTION)
                self._collection_ready = False

            logger.info(
                "Invalidated %d semantic cache entries (task_type=%s)",
                deleted,
                task_type,
            )
            return deleted
        except Exception:
            logger.warning("Semantic cache invalidate failed", exc_info=True)
            return 0

    def stats(self) -> dict[str, Any]:
        """Return cache statistics.

        Keys: ``total_entries``, ``hits``, ``misses``, ``hit_rate``.
        """
        total_entries = 0
        try:
            if self._collection_ready and self._client:
                info = self._client.get_collection(SEMANTIC_CACHE_COLLECTION)
                total_entries = info.points_count or 0
        except Exception:
            pass

        total_requests = self._hits + self._misses
        return {
            "total_entries": total_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total_requests, 4) if total_requests > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache | None:
    """Return the module-level ``SemanticCache`` instance, or ``None``."""
    return _cache


def init_semantic_cache(
    qdrant_client: Any,
    embed_fn: Callable[[str], list[float]],
) -> SemanticCache:
    """Initialise and return the module-level ``SemanticCache``."""
    global _cache  # noqa: PLW0603
    _cache = SemanticCache(qdrant_client, embed_fn)
    return _cache


def semantic_cache_get(task_type: str, query: str) -> dict | None:
    """Look up a cached response (returns ``None`` if disabled or miss)."""
    if not SEMANTIC_CACHE_ENABLED or not _cache:
        return None
    return _cache.get(task_type, query)


def semantic_cache_put(task_type: str, query: str, response: str) -> None:
    """Store a response in the semantic cache (no-op if disabled)."""
    if not SEMANTIC_CACHE_ENABLED or not _cache:
        return
    _cache.put(task_type, query, response)


def semantic_cache_invalidate(task_type: str | None = None) -> int:
    """Invalidate cache entries (returns 0 if cache not initialised)."""
    if not _cache:
        return 0
    return _cache.invalidate(task_type)
