"""Semantic chunking — embedding-based boundary detection.

Instead of splitting text at fixed character/paragraph boundaries, this module
uses embedding similarity to find natural topic shifts and split there.
Falls back to paragraph-based chunking if embeddings are unavailable.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("rag-bridge.semantic_chunker")

SEMANTIC_CHUNKING_ENABLED = os.getenv("SEMANTIC_CHUNKING_ENABLED", "true").lower() == "true"
MIN_CHUNK_CHARS = int(os.getenv("SEMANTIC_MIN_CHUNK_CHARS", "200"))
MAX_CHUNK_CHARS = int(os.getenv("SEMANTIC_MAX_CHUNK_CHARS", "2000"))
SIMILARITY_THRESHOLD = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.75"))
OVERLAP_SENTENCES = int(os.getenv("SEMANTIC_OVERLAP_SENTENCES", "1"))


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using regex."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _group_sentences(sentences: list[str], window: int = 3) -> list[str]:
    """Group sentences into overlapping windows for embedding."""
    groups = []
    for i in range(len(sentences)):
        start = max(0, i - window // 2)
        end = min(len(sentences), i + window // 2 + 1)
        groups.append(" ".join(sentences[start:end]))
    return groups


def semantic_chunk(
    text: str,
    embed_fn=None,
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[str]:
    """Split text into semantic chunks using embedding similarity.

    Args:
        text: The input text to chunk.
        embed_fn: Function (texts) -> (vectors, token_count). If None, falls back to paragraph chunking.
        min_chars: Minimum chunk size in characters.
        max_chars: Maximum chunk size in characters.
        threshold: Cosine similarity threshold below which to split.

    Returns:
        List of text chunks.
    """
    if not text or len(text) < min_chars:
        return [text] if text else []

    if not SEMANTIC_CHUNKING_ENABLED or not embed_fn:
        return _fallback_chunk(text, max_chars)

    sentences = _split_sentences(text)
    if len(sentences) <= 2:
        return [text]

    # Embed sentence groups
    groups = _group_sentences(sentences)
    try:
        vectors, _ = embed_fn(groups)
    except Exception as exc:
        logger.warning("Semantic embedding failed, falling back: %s", exc)
        return _fallback_chunk(text, max_chars)

    if not vectors or len(vectors) != len(groups):
        return _fallback_chunk(text, max_chars)

    # Find split points where consecutive similarity drops below threshold
    split_indices = []
    for i in range(1, len(vectors)):
        sim = _cosine_similarity(vectors[i - 1], vectors[i])
        if sim < threshold:
            split_indices.append(i)

    if not split_indices:
        # No natural breaks found, fall back
        return _fallback_chunk(text, max_chars)

    # Build chunks from split points
    chunks = []
    start = 0
    for idx in split_indices:
        chunk_text = " ".join(sentences[start:idx]).strip()
        if chunk_text:
            chunks.append(chunk_text)
        start = max(0, idx - OVERLAP_SENTENCES)

    # Last chunk
    remainder = " ".join(sentences[start:]).strip()
    if remainder:
        chunks.append(remainder)

    # Merge too-small chunks with neighbors
    merged = _merge_small_chunks(chunks, min_chars, max_chars)
    return merged


def _merge_small_chunks(chunks: list[str], min_chars: int, max_chars: int) -> list[str]:
    """Merge chunks that are below minimum size."""
    if not chunks:
        return chunks
    merged = [chunks[0]]
    for chunk in chunks[1:]:
        if len(merged[-1]) < min_chars and len(merged[-1]) + len(chunk) <= max_chars:
            merged[-1] = merged[-1] + " " + chunk
        else:
            merged.append(chunk)
    # Check the last chunk
    if len(merged) > 1 and len(merged[-1]) < min_chars:
        if len(merged[-2]) + len(merged[-1]) <= max_chars:
            merged[-2] = merged[-2] + " " + merged[-1]
            merged.pop()
    return merged


def _fallback_chunk(text: str, max_chars: int) -> list[str]:
    """Paragraph-based fallback chunking."""
    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # If a single paragraph exceeds max, split by sentences
            if len(para) > max_chars:
                sentences = _split_sentences(para)
                buf = ""
                for s in sentences:
                    if len(buf) + len(s) + 1 <= max_chars:
                        buf = (buf + " " + s).strip()
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = s
                current = buf
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks
