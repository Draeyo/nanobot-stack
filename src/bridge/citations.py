"""Inline citation generation for RAG responses.

Adds numbered citations ([1], [2], ...) to LLM responses and builds
a reference list linking back to source documents.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("rag-bridge.citations")

CITATIONS_ENABLED = os.getenv("CITATIONS_ENABLED", "true").lower() == "true"
MAX_CITATIONS = int(os.getenv("MAX_CITATIONS", "10"))

CITATION_INSTRUCTION = """When using information from the provided context, add inline citations
using [1], [2], etc. to reference the source. At the end of your response, list the sources:

Sources:
[1] source_name - brief description
[2] source_name - brief description

Only cite sources you actually used. Do not invent sources."""


def build_citation_context(
    results: list[dict[str, Any]],
    limit: int = MAX_CITATIONS,
) -> tuple[str, list[dict[str, str]]]:
    """Build a context block with numbered citations.

    Args:
        results: Search results with metadata (source, text, etc.).
        limit: Maximum number of sources to include.

    Returns:
        Tuple of (context_text, source_list) where source_list maps
        citation numbers to source metadata.
    """
    if not CITATIONS_ENABLED or not results:
        return "", []

    sources: list[dict[str, str]] = []
    context_parts = []

    for i, r in enumerate(results[:limit]):
        num = i + 1
        meta = r.get("metadata", r.get("payload", {}))
        text = meta.get("text", r.get("text", ""))
        source_name = meta.get("source", meta.get("filename", f"source_{num}"))
        collection = meta.get("collection", "")

        if not text:
            continue

        context_parts.append(f"[{num}] ({source_name}): {text}")
        sources.append({
            "num": num,
            "source": source_name,
            "collection": collection,
            "chunk_id": r.get("id", ""),
        })

    if not context_parts:
        return "", []

    context = "## Reference Context\n\n" + "\n\n".join(context_parts)
    context += "\n\n" + CITATION_INSTRUCTION
    return context, sources


def extract_used_citations(response_text: str, sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """Extract which citations were actually used in the response."""
    if not response_text or not sources:
        return []

    used_nums = set(int(m) for m in re.findall(r'\[(\d+)\]', response_text))
    return [s for s in sources if s.get("num") in used_nums]


def format_source_footer(sources: list[dict[str, str]]) -> str:
    """Format a source list as a footer for the response."""
    if not sources:
        return ""
    lines = ["\n\n---\n**Sources:**"]
    for s in sources:
        num = s.get("num", "?")
        name = s.get("source", "unknown")
        collection = s.get("collection", "")
        suffix = f" ({collection})" if collection else ""
        lines.append(f"- [{num}] {name}{suffix}")
    return "\n".join(lines)


def enrich_response_with_citations(
    response_text: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Post-process a response to add citation metadata.

    Returns dict with enriched text and citation details.
    """
    if not CITATIONS_ENABLED:
        return {"text": response_text, "citations": []}

    _, sources = build_citation_context(results)
    used = extract_used_citations(response_text, sources)

    return {
        "text": response_text,
        "citations": used,
        "citation_count": len(used),
        "sources_available": len(sources),
    }
