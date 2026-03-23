"""Working memory — fast session-scoped context cache.

Maintains a per-session working memory that avoids redundant prefetch/search
calls within the same conversation. Separate from the long-term vector memory.

Features:
- Session-scoped key-value store with TTL
- Automatic eviction of oldest sessions
- Thread-safe operations
- Proactive context hints based on recent interactions
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("rag-bridge.working_memory")

MAX_SESSIONS = int(os.getenv("WORKING_MEMORY_MAX_SESSIONS", "100"))
SESSION_TTL = float(os.getenv("WORKING_MEMORY_SESSION_TTL", "3600"))  # 1 hour
MAX_ITEMS_PER_SESSION = int(os.getenv("WORKING_MEMORY_MAX_ITEMS", "50"))


class SessionMemory:
    """Working memory for a single session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = time.monotonic()
        self.last_accessed = time.monotonic()
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

        # Conversation context tracking
        self.topics: list[str] = []
        self.entities_mentioned: set[str] = set()
        self.retrieved_chunk_ids: set[str] = set()
        self.query_history: list[str] = []

    def put(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store a value in working memory."""
        with self._lock:
            self.last_accessed = time.monotonic()
            effective_ttl = ttl or SESSION_TTL
            self._data[key] = (time.monotonic() + effective_ttl, value)
            self._data.move_to_end(key)
            # Evict oldest if over limit
            while len(self._data) > MAX_ITEMS_PER_SESSION:
                self._data.popitem(last=False)

    def get(self, key: str) -> Any | None:
        """Get a value from working memory."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._data[key]
                return None
            self.last_accessed = time.monotonic()
            self._data.move_to_end(key)
            return value

    def track_query(self, query: str) -> None:
        """Track a query in the session history."""
        with self._lock:
            self.query_history.append(query)
            if len(self.query_history) > 20:
                self.query_history = self.query_history[-20:]

    def track_retrieval(self, chunk_ids: list[str]) -> None:
        """Track which chunks have been retrieved already."""
        with self._lock:
            self.retrieved_chunk_ids.update(chunk_ids)

    def track_topic(self, topic: str) -> None:
        """Track a conversation topic."""
        with self._lock:
            if topic and topic not in self.topics:
                self.topics.append(topic)
                if len(self.topics) > 10:
                    self.topics = self.topics[-10:]

    def track_entity(self, entity: str) -> None:
        """Track an entity mentioned in conversation."""
        with self._lock:
            self.entities_mentioned.add(entity.lower())

    def is_chunk_seen(self, chunk_id: str) -> bool:
        """Check if a chunk was already retrieved in this session."""
        with self._lock:
            return chunk_id in self.retrieved_chunk_ids

    def get_context_summary(self) -> dict[str, Any]:
        """Return a summary of the working memory state."""
        return {
            "session_id": self.session_id,
            "topics": self.topics,
            "entities": sorted(self.entities_mentioned),
            "queries_count": len(self.query_history),
            "chunks_seen": len(self.retrieved_chunk_ids),
            "items_cached": len(self._data),
        }

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.last_accessed > SESSION_TTL


class WorkingMemoryStore:
    """Global store for all session working memories."""

    def __init__(self):
        self._sessions: OrderedDict[str, SessionMemory] = OrderedDict()
        self._lock = threading.Lock()

    def get_session(self, session_id: str) -> SessionMemory:
        """Get or create a session working memory."""
        if not session_id:
            session_id = "_default"
        with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                if session.is_expired:
                    del self._sessions[session_id]
                else:
                    self._sessions.move_to_end(session_id)
                    return session

            session = SessionMemory(session_id)
            self._sessions[session_id] = session

            # Evict oldest sessions
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)

            return session

    def remove_session(self, session_id: str) -> bool:
        """Remove a session from working memory."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def stats(self) -> dict[str, Any]:
        """Return store statistics."""
        with self._lock:
            active = sum(1 for s in self._sessions.values() if not s.is_expired)
            return {
                "total_sessions": len(self._sessions),
                "active_sessions": active,
                "max_sessions": MAX_SESSIONS,
            }

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count of removed."""
        removed = 0
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired]
            for sid in expired:
                del self._sessions[sid]
                removed += 1
        return removed


# Global singleton
working_memory = WorkingMemoryStore()
