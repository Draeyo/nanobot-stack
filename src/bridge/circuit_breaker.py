"""Lightweight circuit breaker for LLM provider fallback chains.

States:
  CLOSED   — requests pass through normally; failures are counted.
  OPEN     — requests are rejected immediately (profile skipped).
  HALF_OPEN — one probe request is allowed; success closes, failure re-opens.

After ``failure_threshold`` consecutive failures the circuit opens for
``recovery_timeout`` seconds, then transitions to half-open.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any

logger = logging.getLogger("rag-bridge.circuit_breaker")


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout: float = 120.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = State.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = State.HALF_OPEN
                    logger.info("Circuit %s → HALF_OPEN (probe allowed)", self.name)
            return self._state

    @property
    def is_available(self) -> bool:
        s = self.state
        return s in (State.CLOSED, State.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            prev = self._state
            self._failure_count = 0
            self._state = State.CLOSED
            if prev != State.CLOSED:
                logger.info("Circuit %s → CLOSED (success)", self.name)

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == State.HALF_OPEN:
                self._state = State.OPEN
                logger.warning("Circuit %s → OPEN (half-open probe failed)", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = State.OPEN
                logger.warning(
                    "Circuit %s → OPEN (%d consecutive failures, cooldown %.0fs)",
                    self.name, self._failure_count, self.recovery_timeout,
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


class CircuitBreakerRegistry:
    """Thread-safe registry — one breaker per profile name."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 120.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, profile_name: str) -> CircuitBreaker:
        with self._lock:
            if profile_name not in self._breakers:
                self._breakers[profile_name] = CircuitBreaker(
                    name=profile_name,
                    failure_threshold=self.failure_threshold,
                    recovery_timeout=self.recovery_timeout,
                )
            return self._breakers[profile_name]

    def all_status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [cb.to_dict() for cb in self._breakers.values()]
