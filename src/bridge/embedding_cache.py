"""LRU embedding cache with TTL.

Caches embedding vectors keyed by the SHA-256 of the input text.
Identical or repeated queries skip the API call entirely.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any


class EmbeddingCache:
    def __init__(self, max_size: int = 512, ttl_seconds: float = 3600.0):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> list[float] | None:
        k = self._key(text)
        with self._lock:
            entry = self._cache.get(k)
            if entry is None:
                self._misses += 1
                return None
            ts, vector = entry
            if time.monotonic() - ts > self.ttl:
                del self._cache[k]
                self._misses += 1
                return None
            self._cache.move_to_end(k)
            self._hits += 1
            return vector

    def put(self, text: str, vector: list[float]) -> None:
        k = self._key(text)
        with self._lock:
            self._cache[k] = (time.monotonic(), vector)
            self._cache.move_to_end(k)
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def get_many(self, texts: list[str]) -> tuple[dict[int, list[float]], list[int]]:
        """Return (cached results by index, list of uncached indices)."""
        cached: dict[int, list[float]] = {}
        uncached: list[int] = []
        for i, text in enumerate(texts):
            vec = self.get(text)
            if vec is not None:
                cached[i] = vec
            else:
                uncached.append(i)
        return cached, uncached

    def put_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        for text, vec in zip(texts, vectors):
            self.put(text, vec)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
                "ttl_seconds": self.ttl,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
