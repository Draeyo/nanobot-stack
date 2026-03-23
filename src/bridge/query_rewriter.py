"""Query rewriting and expansion for improved RAG retrieval.

Implements:
- HyDE (Hypothetical Document Embeddings): generates a hypothetical answer
  and uses its embedding for retrieval instead of the raw query.
- Multi-perspective rewriting: rewrites the query from different angles
  to increase recall.
- Query expansion with synonyms and related terms.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("rag-bridge.query_rewriter")

HYDE_ENABLED = os.getenv("HYDE_ENABLED", "true").lower() == "true"
MULTI_QUERY_ENABLED = os.getenv("MULTI_QUERY_ENABLED", "true").lower() == "true"
MAX_REWRITE_QUERIES = int(os.getenv("MAX_REWRITE_QUERIES", "3"))

HYDE_PROMPT = """Given this question, write a short paragraph (3-5 sentences) that would be
a perfect answer to the question. Write it as if it were a passage from a relevant document.
Do NOT say "the answer is..." — just write the content directly as factual prose.

Question: {query}"""

MULTI_QUERY_PROMPT = """Given this search query, generate {count} alternative versions of it
that capture different perspectives or phrasings. The goal is to improve retrieval by searching
from multiple angles.

Return ONLY JSON: {{"queries": ["query1", "query2", "query3"]}}

Original query: {query}"""


def generate_hyde_passage(query: str, run_chat_fn) -> str:
    """Generate a hypothetical document passage for HyDE retrieval."""
    if not HYDE_ENABLED:
        return ""
    try:
        result = run_chat_fn("query_rewrite", [
            {"role": "user", "content": HYDE_PROMPT.format(query=query)},
        ], max_tokens=300)
        passage = result.get("text", "").strip()
        logger.debug("HyDE passage generated (%d chars)", len(passage))
        return passage
    except Exception as exc:
        logger.warning("HyDE generation failed: %s", exc)
        return ""


def generate_multi_queries(query: str, run_chat_fn, count: int = MAX_REWRITE_QUERIES) -> list[str]:
    """Generate multiple query perspectives for improved recall."""
    if not MULTI_QUERY_ENABLED:
        return [query]
    try:
        result = run_chat_fn("query_rewrite", [
            {"role": "user", "content": MULTI_QUERY_PROMPT.format(query=query, count=count)},
        ], json_mode=True, max_tokens=400)
        data = json.loads(result.get("text", "{}"))
        queries = data.get("queries", [])
        # Always include the original query first
        all_queries = [query] + [q for q in queries if q and q != query]
        return all_queries[:count + 1]
    except Exception as exc:
        logger.warning("Multi-query generation failed: %s", exc)
        return [query]


def rewrite_query(
    query: str,
    run_chat_fn,
    embed_fn=None,
    mode: str = "hyde",
) -> dict[str, Any]:
    """Rewrite a query for improved retrieval.

    Args:
        query: The original user query.
        run_chat_fn: The chat function for LLM calls.
        embed_fn: Optional embedding function for HyDE.
        mode: 'hyde', 'multi', or 'both'.

    Returns:
        Dict with rewritten queries and optional HyDE embedding.
    """
    result: dict[str, Any] = {"original_query": query, "mode": mode}

    if mode in ("hyde", "both") and HYDE_ENABLED:
        passage = generate_hyde_passage(query, run_chat_fn)
        result["hyde_passage"] = passage
        if passage and embed_fn:
            try:
                vectors, _ = embed_fn([passage])
                result["hyde_vector"] = vectors[0]
            except Exception:
                logger.warning("HyDE embedding failed for passage")

    if mode in ("multi", "both") and MULTI_QUERY_ENABLED:
        queries = generate_multi_queries(query, run_chat_fn)
        result["multi_queries"] = queries

    return result
