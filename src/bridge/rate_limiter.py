"""Simple in-memory token-bucket rate limiter.

Each bucket is identified by a string key (e.g. endpoint name).
Tokens refill at a constant rate. If the bucket is empty the request
is rejected with a 429 status.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import HTTPException


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: maximum number of tokens in the bucket.
            refill_rate: tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def try_consume(self, tokens: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            self._refill()
            return {
                "tokens_available": round(self._tokens, 2),
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
            }


class RateLimiterRegistry:
    """Thread-safe registry of named rate limiters."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def register(self, name: str, capacity: int, refill_rate: float) -> None:
        with self._lock:
            self._buckets[name] = TokenBucket(capacity=capacity, refill_rate=refill_rate)

    def check(self, name: str, tokens: int = 1) -> None:
        """Consume tokens or raise HTTP 429."""
        with self._lock:
            bucket = self._buckets.get(name)
        if bucket is None:
            return  # no limiter registered for this name → allow
        if not bucket.try_consume(tokens):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {name}. Try again shortly.",
            )

    def all_status(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: b.to_dict() for name, b in self._buckets.items()}
