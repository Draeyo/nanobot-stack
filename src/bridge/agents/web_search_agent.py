"""WebSearchAgent — SearXNG-powered web search for nanobot-stack.

Implements Sub-projet D: self-hosted web search with Qdrant result caching
(TTL 6h), SQLite rate limiting, and web_digest briefing section.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

try:
    from .base import AgentBase, AgentResult
except ImportError:
    from agents.base import AgentBase, AgentResult

logger = logging.getLogger("rag-bridge.agents.web_search")

VALID_CATEGORIES = frozenset(
    {"general", "news", "it", "science", "files", "images", "videos"}
)


class WebSearchRateLimitError(Exception):
    """Raised when the hourly rate limit is exceeded."""


class WebSearchUnavailableError(Exception):
    """Raised when SearXNG cannot be reached."""


class WebSearchDisabledError(Exception):
    """Raised when SEARXNG_ENABLED=false and a caller expects an error."""


@dataclass
class SearchResult:
    """A single result returned by SearXNG."""

    url: str
    title: str
    snippet: str
    score: float
    category: str
    engine: str
    fetched_at: str


class WebSearchAgent(AgentBase):
    """Self-hosted SearXNG web search with Qdrant caching and rate limiting."""

    name: str = "web_search"
    description: str = (
        "Self-hosted web search via SearXNG — returns relevant web results "
        "and stores them in Qdrant for same-session RAG reuse"
    )
    tools: list[str] = ["web_search"]
    max_steps: int = 5

    def __init__(
        self,
        run_chat_fn: Callable[..., Any],
        db_path: str | None = None,
        qdrant_client: Any = None,
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        trust_engine: Any = None,
    ) -> None:
        super().__init__(run_chat_fn, tool_registry, trust_engine)

        self.enabled: bool = (
            os.getenv("SEARXNG_ENABLED", "false").lower() in ("1", "true", "yes")
        )
        self.searxng_url: str = os.getenv("SEARXNG_URL", "http://searxng:8080")
        self.max_results: int = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
        self.rate_limit: int = int(os.getenv("WEB_SEARCH_RATE_LIMIT_PER_HOUR", "20"))
        self.result_ttl_hours: int = int(os.getenv("WEB_SEARCH_RESULT_TTL_HOURS", "6"))

        state_dir = os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")
        self._db_path: str = db_path or str(
            __import__("pathlib").Path(state_dir) / "scheduler.db"
        )
        self._qdrant = qdrant_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Full agent pipeline: extract query -> RAG cache check -> search -> synthesise."""
        import litellm  # type: ignore[import]

        if not self.enabled:
            return self._make_result(
                "completed",
                "Web search is disabled (SEARXNG_ENABLED=false).",
            )

        # Step 1: LLM extracts structured query from task
        query = task
        num_results = self.max_results
        cats: list[str] = ["general"]
        use_cache = False
        try:
            extraction = self.run_chat_fn(
                "query_extraction",
                [
                    {
                        "role": "system",
                        "content": (
                            "Extract a web search query from the user task. "
                            "Return ONLY JSON: "
                            '{"query": "...", "num_results": 5, '
                            '"categories": ["general"], "use_cache": false}'
                        ),
                    },
                    {"role": "user", "content": task[:2000]},
                ],
                json_mode=True,
                max_tokens=200,
            )
            parsed = json.loads(extraction["text"])
            query = parsed.get("query", task)
            num_results = int(parsed.get("num_results", self.max_results))
            cats = parsed.get("categories", ["general"])
            use_cache = bool(parsed.get("use_cache", False))
        except Exception as exc:
            logger.warning("Query extraction failed: %s", exc)

        # Step 2: Check RAG cache
        rag_ctx = await self._build_rag_context(query)

        # Step 3: Search if cache is insufficient
        results: list[SearchResult] = []
        if not use_cache:
            try:
                results = await self.search(query, num_results, cats, source="agent")
                rag_ctx = await self._build_rag_context(query)
            except (WebSearchRateLimitError, WebSearchUnavailableError) as exc:
                return self._make_result("failed", str(exc))

        # Step 4: LLM synthesis
        context_block = rag_ctx or "\n".join(
            f"- {r.title}: {r.snippet}" for r in results
        )
        synthesis = task  # fallback
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant de recherche. "
                        "Synthétise les résultats de recherche web ci-dessous "
                        "pour répondre à la tâche de l'utilisateur. "
                        "Cite les sources (URLs) dans ta réponse."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Tâche: {task}\n\nRésultats:\n{context_block[:4000]}",
                },
            ]
            resp = await litellm.acompletion(
                model=os.getenv("WEB_SEARCH_SYNTHESIS_MODEL", "gpt-4o-mini"),
                messages=messages,
                max_tokens=1500,
            )
            synthesis = resp.choices[0].message.content or synthesis
        except Exception as exc:
            logger.warning("Web search synthesis failed: %s", exc)

        self._log_action(
            "web_search",
            {"query": query, "categories": cats},
            f"{len(results)} results",
        )

        return self._make_result(
            "completed",
            synthesis,
            artifacts={"results": [vars(r) for r in results]},
        )

    async def search(
        self,
        query: str,
        num_results: int | None = None,
        categories: list[str] | None = None,
        source: str = "api",
    ) -> list[SearchResult]:
        """Full pipeline: rate check -> SearXNG -> Qdrant -> log -> return results."""
        if not self.enabled:
            return []

        effective_num = min(num_results or self.max_results, self.max_results)
        effective_cats = categories or ["general"]
        t0 = datetime.now(timezone.utc)
        db = sqlite3.connect(self._db_path)
        db.execute("PRAGMA journal_mode=WAL")

        try:
            try:
                self._check_rate_limit(db)
            except WebSearchRateLimitError:
                self._increment_rate_counter(
                    db, query, effective_cats, effective_num,
                    0, None, "rate_limited", source=source,
                )
                db.commit()
                raise

            params = {
                "q": query,
                "format": "json",
                "categories": ",".join(effective_cats),
                "language": "fr-FR",
                "safesearch": 1,
                "pageno": 1,
            }

            try:
                raw = await self._call_searxng(query, params)
            except WebSearchUnavailableError as exc:
                duration_ms = int(
                    (datetime.now(timezone.utc) - t0).total_seconds() * 1000
                )
                self._increment_rate_counter(
                    db, query, effective_cats, effective_num,
                    0, duration_ms, "error",
                    error_message=str(exc), source=source,
                )
                db.commit()
                raise

            raw = raw[:effective_num]
            fetched_at = datetime.now(timezone.utc).isoformat()
            results = [
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=item.get("content", "")[:500],
                    score=float(item.get("score", 0.0)),
                    category=item.get("category", "general"),
                    engine=item.get("engine", ""),
                    fetched_at=fetched_at,
                )
                for item in raw
            ]

            stored = await self._upsert_results(results)
            duration_ms = int(
                (datetime.now(timezone.utc) - t0).total_seconds() * 1000
            )
            self._increment_rate_counter(
                db, query, effective_cats, effective_num,
                stored, duration_ms, "ok", source=source,
            )
            db.commit()
            return results

        finally:
            db.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_searxng(
        self, _query: str, params: dict[str, Any]
    ) -> list[dict]:
        """GET /search on SearXNG, return list of raw result dicts."""
        import httpx  # type: ignore[import]

        url = self.searxng_url.rstrip("/") + "/search"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise WebSearchUnavailableError(
                f"SearXNG unavailable at {self.searxng_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise WebSearchUnavailableError(
                f"SearXNG returned HTTP {exc.response.status_code}"
            ) from exc

        raw = resp.json().get("results", [])
        # Truncate snippets to 500 chars before returning
        for item in raw:
            if "content" in item and len(item["content"]) > 500:
                item["content"] = item["content"][:500]
        return raw

    def _check_rate_limit(self, db: sqlite3.Connection) -> bool:
        """Return True if under limit. Raises WebSearchRateLimitError if over."""
        window_start = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        row = db.execute(
            "SELECT COUNT(*) FROM web_search_log "
            "WHERE created_at >= ? AND status != 'rate_limited'",
            (window_start,),
        ).fetchone()
        count = row[0] if row else 0
        if count >= self.rate_limit:
            raise WebSearchRateLimitError(
                f"Rate limit exceeded: {count}/{self.rate_limit} searches in the last hour"
            )
        return True

    def _increment_rate_counter(
        self,
        db: sqlite3.Connection,
        query: str,
        categories: list[str],
        num_results: int,
        results_stored: int,
        duration_ms: int | None,
        status: str,
        error_message: str | None = None,
        source: str = "api",
    ) -> None:
        """Insert a web_search_log row. Caller is responsible for db.commit()."""
        db.execute(
            "INSERT INTO web_search_log "
            "(id, query, categories, num_results, results_stored, "
            "duration_ms, status, error_message, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                query,
                json.dumps(categories),
                num_results,
                results_stored,
                duration_ms,
                status,
                error_message,
                source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    async def _upsert_results(self, results: list[SearchResult]) -> int:
        """Embed and upsert SearchResults into Qdrant web_search_results collection."""
        if not results or self._qdrant is None:
            return 0

        import litellm  # type: ignore[import]
        from qdrant_client.models import PointStruct  # type: ignore[import]

        now = datetime.now(timezone.utc).isoformat()
        points = []

        for r in results:
            text_to_embed = f"{r.title}. {r.snippet}"
            embed_resp = await litellm.aembedding(
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                input=[text_to_embed],
            )
            vector = embed_resp["data"][0]["embedding"]
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, r.url))

            payload = {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet[:500],
                "score": r.score,
                "category": r.category,
                "engine": r.engine,
                "source": "web_search",
                "fetched_at": r.fetched_at,
                "created_at": now,
            }

            points.append(
                PointStruct(id=point_id, vector=vector, payload=payload)
            )

        self._qdrant.upsert(
            collection_name="web_search_results",
            points=points,
            wait=True,
        )
        return len(points)

    async def _build_rag_context(self, query: str) -> str:
        """Query Qdrant web_search_results and format as numbered context."""
        if self._qdrant is None:
            return ""
        try:
            import litellm  # type: ignore[import]
            embed_resp = await litellm.aembedding(
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                input=[query],
            )
            vector = embed_resp["data"][0]["embedding"]
            hits = self._qdrant.search(
                collection_name="web_search_results",
                query_vector=vector,
                limit=5,
                score_threshold=0.75,
                with_payload=True,
            )
            if not hits:
                return ""
            lines = []
            for i, hit in enumerate(hits, start=1):
                p = hit.payload
                lines.append(
                    f"[{i}] {p.get('title', '')} ({p.get('url', '')})\n"
                    f"{p.get('snippet', '')}"
                )
            return "\n\n".join(lines)
        except Exception as exc:
            logger.warning("_build_rag_context failed: %s", exc)
            return ""

    async def collect_web_digest(
        self,
        topics: list[str] | None = None,
        num_per_topic: int = 3,
    ) -> str:
        """Collect and summarise search results for briefing web_digest section."""
        if not self.enabled:
            return ""

        if not topics:
            return ""

        # Search all topics in parallel
        async def _search_topic(topic: str) -> list[SearchResult]:
            try:
                return await self.search(
                    topic, num_results=num_per_topic,
                    categories=["general", "news"],
                    source="scheduler",
                )
            except (WebSearchRateLimitError, WebSearchUnavailableError) as exc:
                logger.warning("web_digest search for '%s' failed: %s", topic, exc)
                return []

        topic_results = await asyncio.gather(
            *[_search_topic(t) for t in topics]
        )

        # Deduplicate by URL
        seen_urls: set[str] = set()
        all_results: list[SearchResult] = []
        for results in topic_results:
            for r in results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)

        if not all_results:
            return ""

        # Build context for LLM summarization
        context_lines = []
        for r in all_results:
            context_lines.append(f"- [{r.title}]({r.url}) — {r.snippet[:200]}")
        context_block = "\n".join(context_lines)

        try:
            import litellm  # type: ignore[import]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant de veille. Résume les résultats de recherche "
                        "web ci-dessous en un digest structuré en français. "
                        "Groupe par thème. Sois concis."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Résultats de veille web:\n\n{context_block[:4000]}",
                },
            ]
            resp = await litellm.acompletion(
                model=os.getenv("WEB_SEARCH_SYNTHESIS_MODEL", "gpt-4o-mini"),
                messages=messages,
                max_tokens=800,
            )
            return resp.choices[0].message.content or context_block
        except Exception as exc:
            logger.warning("web_digest LLM summarization failed: %s", exc)
            return context_block
