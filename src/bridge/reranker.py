"""Cross-encoder reranker for search results.

Uses a lightweight cross-encoder model (default: BAAI/bge-reranker-v2-m3)
that scores each (query, passage) pair directly.  Falls back to the legacy
lexical+cosine hybrid scoring if the model is unavailable or disabled.

The model is loaded lazily on first use and cached in memory.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from typing import Any

logger = logging.getLogger("rag-bridge.reranker")

RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu")
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "16"))

RERANKER_UNLOAD_AFTER = int(os.getenv("RERANKER_UNLOAD_AFTER", "300"))  # seconds of inactivity

_model = None
_model_lock = threading.Lock()
_model_loaded = False
_model_failed = False
_last_used = 0.0


def _load_model():
    global _model, _model_loaded, _model_failed, _last_used
    if _model_failed:
        return
    if _model_loaded and _model is not None:
        _last_used = time.monotonic()
        return
    with _model_lock:
        if _model_failed:
            return
        if _model_loaded and _model is not None:
            _last_used = time.monotonic()
            return
        if not RERANKER_ENABLED:
            _model_failed = True
            logger.info("Cross-encoder reranker disabled via RERANKER_ENABLED=false")
            return
        try:
            from sentence_transformers import CrossEncoder
            import resource
            mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            logger.info("Loading cross-encoder: %s (device=%s)", RERANKER_MODEL, RERANKER_DEVICE)
            _model = CrossEncoder(RERANKER_MODEL, device=RERANKER_DEVICE)
            _model_loaded = True
            _last_used = time.monotonic()
            mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            logger.info("Cross-encoder loaded (+%dMB RSS)", (mem_after - mem_before) // 1024)
        except Exception as exc:
            _model_failed = True
            logger.warning("Failed to load cross-encoder: %s", exc)


def _maybe_unload():
    """Unload the model after RERANKER_UNLOAD_AFTER seconds of inactivity to free RAM."""
    global _model, _model_loaded
    if not _model_loaded or _model is None:
        return
    if RERANKER_UNLOAD_AFTER <= 0:
        return  # 0 = never unload
    if time.monotonic() - _last_used > RERANKER_UNLOAD_AFTER:
        with _model_lock:
            if _model is not None and time.monotonic() - _last_used > RERANKER_UNLOAD_AFTER:
                logger.info("Unloading cross-encoder after %ds inactivity", RERANKER_UNLOAD_AFTER)
                _model = None
                _model_loaded = False  # will re-load on next use


# ---------------------------------------------------------------------------
# Legacy fallback scoring (from v6)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[a-zA-Z0-9_\-]{2,}", text.lower())


def _lexical_score(query: str, text: str) -> float:
    q = Counter(_tokenize(query))
    t = Counter(_tokenize(text))
    if not q or not t:
        return 0.0
    overlap = sum(min(q[k], t.get(k, 0)) for k in q)
    return overlap / max(1, sum(q.values()))


def _legacy_rerank(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hybrid cosine + lexical + path-boost reranking (v6 fallback)."""
    for row in results:
        payload = row["payload"]
        text = payload.get("text", "")
        path = payload.get("path", "")
        lscore = _lexical_score(query, text)
        pboost = 0.15 if any(tok in path.lower() for tok in _tokenize(query)) else 0.0
        row["lexical_score"] = lscore
        row["path_boost"] = pboost
        row["reranker"] = "legacy"
        row["final_score"] = 0.7 * row["score"] + 0.3 * lscore + pboost
    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Cross-encoder reranking
# ---------------------------------------------------------------------------

def rerank(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rerank search results using cross-encoder or legacy fallback."""
    if not results:
        return results

    _maybe_unload()  # free RAM if idle too long
    _load_model()

    if _model is None:
        return _legacy_rerank(query, results)

    # Build (query, passage) pairs
    pairs = [(query, row["payload"].get("text", "")) for row in results]

    try:
        scores = _model.predict(pairs, batch_size=RERANKER_BATCH_SIZE)
        for row, ce_score in zip(results, scores):
            row["cross_encoder_score"] = float(ce_score)
            row["reranker"] = "cross_encoder"
            # Blend: 60% cross-encoder, 30% cosine, 10% path boost
            path = row["payload"].get("path", "")
            pboost = 0.1 if any(tok in path.lower() for tok in _tokenize(query)) else 0.0
            row["final_score"] = 0.6 * float(ce_score) + 0.3 * row["score"] + pboost
        results.sort(key=lambda x: x["final_score"], reverse=True)
        return results
    except Exception as exc:
        logger.warning("Cross-encoder scoring failed, falling back to legacy: %s", exc)
        return _legacy_rerank(query, results)


def is_available() -> dict[str, Any]:
    """Return reranker status for /healthz and /selftest."""
    _load_model()
    return {
        "enabled": RERANKER_ENABLED,
        "model": RERANKER_MODEL,
        "loaded": _model_loaded,
        "failed": _model_failed,
        "device": RERANKER_DEVICE,
    }
