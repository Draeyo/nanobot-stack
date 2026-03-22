"""Token usage optimization.

- LLM response cache with normalized keys (punctuation/case insensitive)
- Token counting and cost estimation per endpoint (chat + embeddings)
- Cost-weighted adaptive context budget
- Merged conversation-hook prompt builder
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("rag-bridge.token_optimizer")

LLM_CACHE_SIZE = int(os.getenv("LLM_CACHE_SIZE", "256"))
LLM_CACHE_TTL = float(os.getenv("LLM_CACHE_TTL", "300"))

CACHEABLE_TASKS = {
    "classify_query", "structured_extraction", "vision_describe", "remember_extract",
}


def _normalize_text(text: str) -> str:
    """Normalize text for cache key: lowercase, strip punctuation, collapse whitespace."""
    t = text.lower()
    t = re.sub(r"[^\w\s]", "", t)  # strip punctuation
    t = re.sub(r"\s+", " ", t).strip()
    return t


class LLMResponseCache:
    def __init__(self, max_size: int = LLM_CACHE_SIZE, ttl: float = LLM_CACHE_TTL):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(task_type: str, messages: list[dict[str, str]]) -> str:
        # Normalize each message content for cache-friendliness
        parts = [task_type]
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                parts.append(_normalize_text(content))
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(_normalize_text(p.get("text", "")))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def get(self, task_type: str, messages: list[dict[str, str]]) -> dict | None:
        if task_type not in CACHEABLE_TASKS:
            return None
        k = self._key(task_type, messages)
        with self._lock:
            entry = self._cache.get(k)
            if not entry:
                self._misses += 1
                return None
            ts, result = entry
            if time.monotonic() - ts > self.ttl:
                del self._cache[k]
                self._misses += 1
                return None
            self._cache.move_to_end(k)
            self._hits += 1
            return {**result, "_cached": True}

    def put(self, task_type: str, messages: list[dict[str, str]], result: dict) -> None:
        if task_type not in CACHEABLE_TASKS:
            return
        k = self._key(task_type, messages)
        with self._lock:
            self._cache[k] = (time.monotonic(), result)
            self._cache.move_to_end(k)
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache), "max_size": self.max_size,
                "hits": self._hits, "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


# ---------------------------------------------------------------------------
# Token counting (language-aware)
# ---------------------------------------------------------------------------
_CJK_RANGES = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')

def estimate_tokens(text: str) -> int:
    """Approximate token count, adapted for different content types.

    - English/Latin: ~4 chars per token
    - CJK (Japanese, Chinese, Korean): ~1.5 chars per token
    - Code: ~3.5 chars per token (more whitespace/symbols)
    - Mixed: weighted average based on char class distribution
    """
    if not text:
        return 0
    n = len(text)
    cjk_chars = len(_CJK_RANGES.findall(text))
    if cjk_chars > n * 0.3:
        # Primarily CJK text
        return max(1, int(n / 1.5))
    if text.count('\n') > n / 80 and any(c in text for c in '{}();='):
        # Looks like code
        return max(1, int(n / 3.5))
    return max(1, n // 4)

def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for m in messages:
        total += 4  # overhead
        content = m.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    total += 1000
    return total


# ---------------------------------------------------------------------------
# Cost estimation (2025 pricing $/1M tokens)
# ---------------------------------------------------------------------------
COST_MAP: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "anthropic/claude-sonnet-4-20250514": (3.00, 15.00),
    "text-embedding-3-large": (0.13, 0.0),
    "ollama/qwen2.5:7b": (0.0, 0.0),
    "ollama/nomic-embed-text": (0.0, 0.0),
}

def _lookup_cost(model: str) -> tuple[float, float]:
    for prefix in ("openrouter/openai/", "openrouter/anthropic/", "openrouter/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
    return COST_MAP.get(model, (1.0, 3.0))

def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    c = _lookup_cost(model)
    return (input_tokens * c[0] + output_tokens * c[1]) / 1_000_000


# ---------------------------------------------------------------------------
# Token tracker (chat + embeddings)
# ---------------------------------------------------------------------------
class TokenTracker:
    """Tracks cumulative token usage. Persists to JSONL for cross-restart analysis."""

    def __init__(self, persist_path: str | None = None):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path or os.getenv(
            "TOKEN_STATS_PATH",
            os.path.join(os.getenv("RAG_HOME", "/opt/nanobot-stack/rag-bridge"), "state", "token_stats.jsonl"),
        )
        self._dirty = False
        self._load()

    def _load(self) -> None:
        """Load last session stats from JSONL (last line)."""
        try:
            if os.path.exists(self._persist_path):
                import json as _json
                with open(self._persist_path) as f:
                    lines = f.readlines()
                if lines:
                    last = _json.loads(lines[-1])
                    if last.get("type") == "session_end":
                        for entry in last.get("by_endpoint_model", []):
                            key = f"{entry['endpoint']}|{entry['model']}"
                            self._data[key] = entry
        except Exception:
            pass

    def record(self, endpoint: str, model: str, input_tokens: int, output_tokens: int = 0) -> None:
        cost = estimate_cost(model, input_tokens, output_tokens)
        with self._lock:
            key = f"{endpoint}|{model}"
            if key not in self._data:
                self._data[key] = {"endpoint": endpoint, "model": model, "calls": 0,
                                   "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            d = self._data[key]
            d["calls"] += 1
            d["input_tokens"] += input_tokens
            d["output_tokens"] += output_tokens
            d["cost_usd"] += cost
            self._dirty = True

    def stats(self) -> dict:
        with self._lock:
            entries = list(self._data.values())
            return {
                "total_calls": sum(e["calls"] for e in entries),
                "total_cost_usd": round(sum(e["cost_usd"] for e in entries), 4),
                "by_endpoint_model": sorted(entries, key=lambda x: x["cost_usd"], reverse=True),
            }

    def reset(self):
        self.flush()
        with self._lock:
            self._data.clear()
            self._dirty = False

    def flush(self) -> None:
        """Persist current stats to JSONL file."""
        if not self._dirty:
            return
        with self._lock:
            try:
                import json as _json
                from datetime import datetime, timezone
                os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
                entry = {
                    "type": "session_end",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **self.stats(),
                }
                with open(self._persist_path, "a") as f:
                    f.write(_json.dumps(entry) + "\n")
                self._dirty = False
            except Exception as exc:
                logger.warning("Failed to persist token stats: %s", exc)


# ---------------------------------------------------------------------------
# Cost-weighted adaptive context budget
# ---------------------------------------------------------------------------
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4.1-mini": 128000,
    "gpt-4.1": 128000,
    "anthropic/claude-sonnet-4-20250514": 200000,
    "ollama/qwen2.5:7b": 32000,
}

def compute_context_budget(
    model: str,
    conversation_tokens: int,
    max_output_tokens: int = 2000,
    profile_tokens: int = 200,
) -> int:
    """Token budget for RAG context, weighted by model cost.

    Cheaper models get a larger context budget (we can afford more tokens).
    Expensive models get a tighter budget to save cost.
    """
    window = 32000
    for m, w in MODEL_CONTEXT_WINDOWS.items():
        if m in model:
            window = w
            break

    reserved = conversation_tokens + profile_tokens + max_output_tokens
    margin = int(window * 0.10)
    raw_available = window - reserved - margin

    # Cost weighting: cheap models (< $1/M input) get full budget,
    # expensive models (> $2/M) get budget reduced proportionally
    input_cost = _lookup_cost(model)[0]
    if input_cost <= 0.5:
        cost_factor = 1.0  # cheap or free → full budget
    elif input_cost <= 2.0:
        cost_factor = 0.7  # moderate
    else:
        cost_factor = 0.5  # expensive → halve the context

    budget = int(raw_available * cost_factor)
    return max(500, min(budget, 8000))


def tokens_to_chars(tokens: int) -> int:
    return tokens * 4


# ---------------------------------------------------------------------------
# Resolve actual model name from task_type (reads router config)
# ---------------------------------------------------------------------------
_router_cache: dict[str, Any] = {}
_router_mtime: float = 0.0

def _load_router_config() -> dict[str, Any]:
    """Load model_router.json with mtime cache."""
    global _router_cache, _router_mtime
    import json, pathlib
    paths = [
        pathlib.Path(os.getenv("RAG_HOME", "/opt/nanobot-stack/rag-bridge")) / "model_router.json",
        pathlib.Path(__file__).parent / "model_router.json",
    ]
    for p in paths:
        if p.exists():
            mt = p.stat().st_mtime
            if mt != _router_mtime:
                _router_cache = json.loads(p.read_text())
                _router_mtime = mt
            return _router_cache
    return {}


def resolve_model_for_task(task_type: str) -> str:
    """Get the first configured model name for a task_type.

    Falls back to 'gpt-4.1-mini' if not found.
    """
    router = _load_router_config()
    chain = router.get("task_routes", {}).get(task_type, [])
    profiles = router.get("profiles", {})
    for profile_name in chain:
        profile = profiles.get(profile_name, {})
        model = profile.get("model", "")
        if model:
            return model
    return "gpt-4.1-mini"


# ---------------------------------------------------------------------------
# Merged conversation-hook prompt (facts + profile in one call)
# ---------------------------------------------------------------------------
MERGED_EXTRACT_PROMPT = """Analyze this conversation and extract TWO things:

1. DURABLE FACTS worth remembering for future conversations (decisions, preferences, project context, people, deadlines).
2. USER PROFILE UPDATES (if the user expressed any preference about language, communication style, expertise, or topics of interest).

Output ONLY JSON:
{
  "facts": [{"text": "...", "subject": "...", "tags": [...], "importance": "high|medium|low"}],
  "conversation_summary": "2-3 sentence summary",
  "decisions": ["..."],
  "action_items": ["..."],
  "profile_updates": {"field": "value"}
}

profile_updates fields: name, language, style (brief/detailed/technical), expertise (list), context, preferences (dict).
Only include profile_updates if there's clear evidence. Empty dict {} if nothing to update.
Do NOT extract secrets, tokens, passwords, or transient troubleshooting details."""

def build_merged_extract_messages(conversation: list[dict[str, str]]) -> list[dict[str, str]]:
    """Single prompt that extracts facts AND profile updates (saves one LLM call)."""
    conv_text = "\n".join(f"[{m['role']}]: {m['content']}" for m in conversation[-20:])
    return [
        {"role": "system", "content": MERGED_EXTRACT_PROMPT},
        {"role": "user", "content": conv_text},
    ]
