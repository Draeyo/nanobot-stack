"""Simple in-memory token-bucket rate limiter with per-user support.

Each bucket is identified by a string key (e.g. endpoint name or user:endpoint).
Tokens refill at a constant rate. If the bucket is empty the request
is rejected with a 429 status.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import HTTPException, Request


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
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
    """Thread-safe registry of named rate limiters with per-user support."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._configs: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, capacity: int, refill_rate: float) -> None:
        with self._lock:
            self._buckets[name] = TokenBucket(capacity=capacity, refill_rate=refill_rate)
            self._configs[name] = (capacity, refill_rate)

    def check(self, name: str, tokens: int = 1) -> None:
        """Consume tokens or raise HTTP 429."""
        with self._lock:
            bucket = self._buckets.get(name)
        if bucket is None:
            return
        if not bucket.try_consume(tokens):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {name}. Try again shortly.",
            )

    def check_per_user(self, name: str, user_id: str, tokens: int = 1) -> None:
        """Per-user rate limiting. Creates a separate bucket per user."""
        if not user_id:
            self.check(name, tokens)
            return

        key = f"{name}:user:{user_id}"
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                config = self._configs.get(name, (30, 0.5))
                bucket = TokenBucket(capacity=config[0], refill_rate=config[1])
                self._buckets[key] = bucket

                # Evict old user buckets if too many
                user_keys = [k for k in self._buckets if ":user:" in k]
                if len(user_keys) > 500:
                    oldest = user_keys[0]
                    del self._buckets[oldest]

        if not bucket.try_consume(tokens):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {name} (user: {user_id}). Try again shortly.",
            )

    def all_status(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: b.to_dict() for name, b in self._buckets.items() if ":user:" not in name}


def extract_user_id(request: Request) -> str:
    """Extract a user identifier from the request for per-user rate limiting."""
    user = request.headers.get("X-Forwarded-User", "")
    if user:
        return user
    user = request.headers.get("X-User-Id", "")
    if user:
        return user
    if request.client:
        return request.client.host
    return ""
