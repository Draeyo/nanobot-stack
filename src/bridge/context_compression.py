"""Context window compression and management.

- Progressive conversation summarization with adaptive recent-message count
- Embedding-based deduplication (reuses search embeddings)
- Chunk summary pre-storage at ingestion for lighter context injection
- Token-budget-aware context assembly
- Session summary persistence
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from typing import Any, Callable

from token_optimizer import estimate_tokens, estimate_messages_tokens, tokens_to_chars

logger = logging.getLogger("rag-bridge.context_compression")

# ---------------------------------------------------------------------------
# Session summary cache (persists across messages in the same session)
# ---------------------------------------------------------------------------
_summary_cache: dict[str, str] = {}  # session_id → summary
_summary_lock = threading.Lock()

def get_cached_summary(session_id: str) -> str | None:
    with _summary_lock:
        return _summary_cache.get(session_id)

def set_cached_summary(session_id: str, summary: str) -> None:
    with _summary_lock:
        _summary_cache[session_id] = summary
        # Evict oldest if too many sessions
        if len(_summary_cache) > 100:
            oldest = next(iter(_summary_cache))
            del _summary_cache[oldest]

# ---------------------------------------------------------------------------
# Adaptive conversation summarization
# ---------------------------------------------------------------------------
SUMMARIZE_TOKEN_THRESHOLD = int(os.getenv("SUMMARIZE_TOKEN_THRESHOLD", "3000"))
KEEP_RECENT_TOKEN_BUDGET = int(os.getenv("KEEP_RECENT_TOKEN_BUDGET", "2000"))

SUMMARIZE_PROMPT = """Summarize this conversation history concisely. Preserve:
- Key decisions and conclusions
- Important facts and context
- Action items and open questions
- User preferences expressed
Be concise (under 300 words). Write in the same language as the conversation."""


def needs_summarization(messages: list[dict[str, str]]) -> bool:
    """Token-based check (not message count) — adapts to message length."""
    user_assistant = [m for m in messages if m.get("role") in ("user", "assistant")]
    return estimate_messages_tokens(user_assistant) > SUMMARIZE_TOKEN_THRESHOLD


def split_conversation(messages: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split into (old messages to summarize, recent messages to keep verbatim).

    Keeps recent messages that fit within KEEP_RECENT_TOKEN_BUDGET instead of
    a fixed count. This adapts: short messages → more kept, long messages → fewer kept.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    user_assistant = [m for m in messages if m.get("role") in ("user", "assistant")]

    # Walk backwards from end, collecting recent messages within budget
    budget = KEEP_RECENT_TOKEN_BUDGET
    split_idx = len(user_assistant)
    for i in range(len(user_assistant) - 1, -1, -1):
        cost = estimate_tokens(user_assistant[i].get("content", "")) + 4
        if budget - cost < 0 and split_idx < len(user_assistant):
            break
        budget -= cost
        split_idx = i

    # Keep at least 2 recent messages
    split_idx = min(split_idx, max(0, len(user_assistant) - 2))

    old = user_assistant[:split_idx]
    recent = user_assistant[split_idx:]
    return old, system_msgs + recent


def build_summarize_messages(old_messages: list[dict[str, str]]) -> list[dict[str, str]]:
    old_text = "\n".join(f"[{m['role']}]: {m['content']}" for m in old_messages)
    return [
        {"role": "system", "content": SUMMARIZE_PROMPT},
        {"role": "user", "content": old_text},
    ]


def inject_summary(summary: str, recent_messages: list[dict[str, str]]) -> list[dict[str, str]]:
    summary_msg = {"role": "system", "content": f"## Previous conversation summary\n{summary}"}
    result = []
    inserted = False
    for m in recent_messages:
        if m["role"] != "system" and not inserted:
            result.append(summary_msg)
            inserted = True
        result.append(m)
    if not inserted:
        result.insert(0, summary_msg)
    return result


# ---------------------------------------------------------------------------
# Embedding-based deduplication
# ---------------------------------------------------------------------------

def deduplicate_by_embedding(
    retrieval_results: list[dict[str, Any]],
    query_embedding: list[float],
    recent_messages: list[dict[str, str]],
    embed_fn: Callable[[list[str]], tuple[list[list[float]], Any]] | None = None,
    similarity_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Remove retrieval results that are semantically redundant with recent conversation.

    Uses batch embedding (single API call) for all chunks + conversation text.
    Falls back to n-gram overlap if embedding is not available.
    """
    if not retrieval_results or not recent_messages:
        return retrieval_results

    conv_text = " ".join(m.get("content", "") for m in recent_messages if m.get("role", "") in ("user", "assistant"))
    if not conv_text.strip():
        return retrieval_results

    # Try embedding-based dedup with batched embedding (single API call)
    if embed_fn is not None:
        try:
            # Collect all texts to embed in one batch: [conversation, chunk0, chunk1, ...]
            chunk_texts = [r.get("payload", {}).get("text", "") for r in retrieval_results]
            all_texts = [conv_text] + [t for t in chunk_texts if t]

            # Single embedding API call for everything
            all_vectors, _ = embed_fn(all_texts)
            conv_vec = all_vectors[0]
            chunk_vecs = all_vectors[1:]

            filtered = []
            vec_idx = 0
            for r in retrieval_results:
                chunk_text = r.get("payload", {}).get("text", "")
                if not chunk_text:
                    filtered.append(r)
                    continue
                sim = _cosine_sim(conv_vec, chunk_vecs[vec_idx])
                vec_idx += 1
                r["conv_similarity"] = round(sim, 3)
                if sim < similarity_threshold:
                    filtered.append(r)
                else:
                    logger.debug("Dedup: dropped chunk (sim=%.3f): %s...", sim, chunk_text[:60])
            return filtered
        except Exception:
            pass

    # Fallback: n-gram overlap
    return _ngram_dedup(retrieval_results, conv_text)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _ngram_dedup(results: list[dict[str, Any]], conv_text: str, threshold: float = 0.3) -> list[dict[str, Any]]:
    conv_fps = _text_fingerprints(conv_text)
    if not conv_fps:
        return results
    filtered = []
    for r in results:
        text = r.get("payload", {}).get("text", "")
        chunk_fps = _text_fingerprints(text)
        if not chunk_fps:
            filtered.append(r)
            continue
        overlap = len(conv_fps & chunk_fps) / max(1, len(chunk_fps))
        if overlap < threshold:
            filtered.append(r)
            r["dedup_overlap"] = round(overlap, 3)
    return filtered


def _text_fingerprints(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    if len(words) < n:
        return {" ".join(words)}
    return {" ".join(words[i:i+n]) for i in range(len(words) - n + 1)}


# ---------------------------------------------------------------------------
# Chunk summary generation (for pre-storage at ingestion time)
# ---------------------------------------------------------------------------
CHUNK_SUMMARY_PROMPT = """Summarize this text chunk in 1-2 sentences for use as a compact search snippet. Keep key facts, names, and decisions. Be very concise."""

def build_chunk_summary_messages(chunk_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": CHUNK_SUMMARY_PROMPT},
        {"role": "user", "content": chunk_text},
    ]


# ---------------------------------------------------------------------------
# Context assembly with token budget
# ---------------------------------------------------------------------------

def assemble_context(
    profile_block: str,
    retrieval_results: list[dict[str, Any]],
    token_budget: int,
    recent_messages: list[dict[str, str]] | None = None,
    embed_fn: Callable | None = None,
    use_summaries: bool = False,
) -> str:
    """Assemble context within a token budget, with optional dedup and summary mode."""
    parts: list[str] = []
    budget_remaining = token_budget

    # 1. Profile (always)
    if profile_block:
        pt = estimate_tokens(profile_block)
        if pt < budget_remaining:
            parts.append(profile_block)
            budget_remaining -= pt

    # 2. Deduplicate
    if recent_messages and embed_fn:
        retrieval_results = deduplicate_by_embedding(retrieval_results, [], recent_messages, embed_fn)
    elif recent_messages:
        retrieval_results = _ngram_dedup(
            retrieval_results,
            " ".join(m.get("content", "") for m in recent_messages if m.get("role", "") in ("user", "assistant")),
        )

    # 3. Assemble chunks within budget
    char_budget = tokens_to_chars(budget_remaining)
    total_chars = 0
    items = []
    for r in retrieval_results:
        payload = r.get("payload", {})
        # Use pre-stored summary if available and use_summaries=True
        if use_summaries and payload.get("summary"):
            text = payload["summary"]
        else:
            text = payload.get("text", "")
        title = payload.get("title", "")
        path = payload.get("path", "")
        if total_chars + len(text) > char_budget:
            remaining = char_budget - total_chars
            if remaining > 200:
                text = text[:remaining] + "…"
            else:
                break
        label = title or path or "memory"
        items.append(f"- [{label}]: {text}")
        total_chars += len(text)

    if items:
        parts.append("## Relevant context from memory\n" + "\n".join(items))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Slim snippets for /ask (strip metadata)
# ---------------------------------------------------------------------------

def slim_snippets(results: list[dict[str, Any]], max_text_chars: int = 1500) -> list[dict[str, str]]:
    return [
        {
            "text": r.get("payload", {}).get("text", "")[:max_text_chars],
            "source": r.get("payload", {}).get("title") or r.get("payload", {}).get("path", ""),
            "score": round(r.get("final_score", r.get("score", 0)), 3),
        }
        for r in results
    ]
