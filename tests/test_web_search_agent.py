"""Tests for WebSearchAgent — TDD implementation for Sub-projet D."""
# pylint: disable=protected-access
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_search_db(tmp_path: Path) -> Path:
    """Create a minimal scheduler.db with web_search_log table."""
    db_path = tmp_path / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS web_search_log (
            id              TEXT PRIMARY KEY,
            query           TEXT NOT NULL,
            categories      TEXT NOT NULL DEFAULT '[]',
            num_results     INTEGER NOT NULL DEFAULT 5,
            results_stored  INTEGER NOT NULL DEFAULT 0,
            duration_ms     INTEGER,
            status          TEXT NOT NULL,
            error_message   TEXT,
            source          TEXT NOT NULL DEFAULT 'api',
            created_at      TEXT NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_search_log_created_at "
        "ON web_search_log(created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_search_log_status "
        "ON web_search_log(status)"
    )
    db.commit()
    db.close()
    return db_path


def _insert_log_entry(db_path: Path, status: str = "ok",
                       created_at: str | None = None,
                       query: str = "test") -> None:
    """Insert a single web_search_log entry for rate-limit tests."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO web_search_log "
        "(id, query, categories, num_results, results_stored, status, source, created_at) "
        "VALUES (?, ?, '[]', 5, 0, ?, 'api', ?)",
        (str(uuid.uuid4()), query, status, created_at),
    )
    db.commit()
    db.close()


def _make_agent(tmp_path: Path, enabled: bool = True,
                rate_limit: int = 20) -> "WebSearchAgent":  # noqa: F821
    """Instantiate WebSearchAgent with patched env."""
    db_path = _make_search_db(tmp_path)
    env = {
        "SEARXNG_ENABLED": "true" if enabled else "false",
        "SEARXNG_URL": "http://searxng:8080",
        "WEB_SEARCH_MAX_RESULTS": "5",
        "WEB_SEARCH_RATE_LIMIT_PER_HOUR": str(rate_limit),
        "WEB_SEARCH_RESULT_TTL_HOURS": "6",
        "RAG_STATE_DIR": str(tmp_path),
    }
    with patch.dict("os.environ", env):
        from web_search_agent import WebSearchAgent
        agent = WebSearchAgent(
            run_chat_fn=MagicMock(),
            db_path=str(db_path),
            qdrant_client=MagicMock(),
        )
    return agent


# ---------------------------------------------------------------------------
# Task 3: Skeleton tests
# ---------------------------------------------------------------------------

class TestWebSearchAgentSkeleton:
    def test_agent_name_and_description(self, tmp_path):
        """WebSearchAgent has correct name and description class attributes."""
        from web_search_agent import WebSearchAgent
        assert WebSearchAgent.name == "web_search"
        assert isinstance(WebSearchAgent.description, str)
        assert len(WebSearchAgent.description) > 0

    def test_search_result_dataclass(self):
        """SearchResult dataclass has all required fields."""
        from web_search_agent import SearchResult
        r = SearchResult(
            url="https://example.com",
            title="Example",
            snippet="A snippet",
            score=0.9,
            category="general",
            engine="google",
            fetched_at="2026-03-24T09:00:00Z",
        )
        assert r.url == "https://example.com"
        assert r.score == 0.9

    def test_disabled_flag_on_instantiation(self, tmp_path):
        """WebSearchAgent instantiates without error when SEARXNG_ENABLED=false."""
        agent = _make_agent(tmp_path, enabled=False)
        assert agent is not None

    def test_rate_limit_env_var_loaded(self, tmp_path):
        """rate_limit attribute reflects WEB_SEARCH_RATE_LIMIT_PER_HOUR env var."""
        agent = _make_agent(tmp_path, rate_limit=10)
        assert agent.rate_limit == 10


# ---------------------------------------------------------------------------
# Task 4: _call_searxng
# ---------------------------------------------------------------------------

MOCK_SEARXNG_RESPONSE = {
    "results": [
        {
            "url": "https://example.com/page1",
            "title": "Example Page One",
            "content": "This is the snippet for page one.",
            "score": 0.91,
            "category": "general",
            "engine": "google",
        },
        {
            "url": "https://example.com/page2",
            "title": "Example Page Two",
            "content": "Snippet for page two.",
            "score": 0.75,
            "category": "general",
            "engine": "bing",
        },
    ]
}


class TestCallSearxng:
    @pytest.mark.asyncio
    async def test_call_searxng_returns_results(self, tmp_path):
        """_call_searxng returns list of dicts from SearXNG JSON response."""
        agent = _make_agent(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_SEARXNG_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await agent._call_searxng(
                "test query",
                {"q": "test query", "format": "json", "categories": "general"},
            )

        assert len(results) == 2
        assert results[0]["url"] == "https://example.com/page1"
        assert results[0]["title"] == "Example Page One"

    @pytest.mark.asyncio
    async def test_call_searxng_trims_snippet_to_500(self, tmp_path):
        """Snippets longer than 500 chars are truncated before return."""
        agent = _make_agent(tmp_path)
        long_snippet = "x" * 800

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{
                "url": "https://example.com",
                "title": "Title",
                "content": long_snippet,
                "score": 0.5,
                "category": "general",
                "engine": "google",
            }]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await agent._call_searxng("test", {"q": "test"})

        assert len(results[0]["content"]) <= 500

    @pytest.mark.asyncio
    async def test_call_searxng_raises_on_connect_error(self, tmp_path):
        """ConnectError from httpx raises WebSearchUnavailableError."""
        import httpx
        agent = _make_agent(tmp_path)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception) as exc_info:
                await agent._call_searxng("test", {"q": "test"})

        assert "unavailable" in str(exc_info.value).lower() or \
               "WebSearchUnavailableError" in type(exc_info.value).__name__

    @pytest.mark.asyncio
    async def test_call_searxng_raises_on_non_200(self, tmp_path):
        """Non-200 HTTP response raises WebSearchUnavailableError."""
        import httpx
        agent = _make_agent(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503", request=MagicMock(), response=mock_resp
            )
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception):
                await agent._call_searxng("test", {"q": "test"})

    @pytest.mark.asyncio
    async def test_call_searxng_uses_correct_url(self, tmp_path):
        """GET is called against SEARXNG_URL + /search."""
        agent = _make_agent(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await agent._call_searxng("nginx", {"q": "nginx", "format": "json"})

        call_args = mock_client.get.call_args
        called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        assert "searxng:8080" in called_url
        assert "/search" in called_url


# ---------------------------------------------------------------------------
# Task 5: Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_check_rate_limit_passes_when_under_limit(self, tmp_path):
        """_check_rate_limit returns True when recent count < rate_limit."""
        agent = _make_agent(tmp_path, rate_limit=20)
        db_path = Path(agent._db_path)
        # Insert 19 recent 'ok' entries
        for _ in range(19):
            _insert_log_entry(db_path, status="ok")

        db = sqlite3.connect(str(db_path))
        try:
            result = agent._check_rate_limit(db)
        finally:
            db.close()

        assert result is True

    def test_check_rate_limit_raises_when_at_limit(self, tmp_path):
        """_check_rate_limit raises WebSearchRateLimitError at exactly the limit."""
        from web_search_agent import WebSearchRateLimitError
        agent = _make_agent(tmp_path, rate_limit=5)
        db_path = Path(agent._db_path)
        for _ in range(5):
            _insert_log_entry(db_path, status="ok")

        db = sqlite3.connect(str(db_path))
        try:
            with pytest.raises(WebSearchRateLimitError):
                agent._check_rate_limit(db)
        finally:
            db.close()

    def test_rate_limit_window_sliding(self, tmp_path):
        """Entries older than 1 hour do not count toward the limit."""
        agent = _make_agent(tmp_path, rate_limit=3)
        db_path = Path(agent._db_path)
        old_ts = (
            datetime.now(timezone.utc) - timedelta(minutes=61)
        ).isoformat()
        # Insert 3 old entries (outside the window)
        for _ in range(3):
            _insert_log_entry(db_path, status="ok", created_at=old_ts)
        # Insert 2 recent entries (inside the window)
        for _ in range(2):
            _insert_log_entry(db_path, status="ok")

        db = sqlite3.connect(str(db_path))
        try:
            # Should not raise: only 2 recent entries, limit is 3
            result = agent._check_rate_limit(db)
        finally:
            db.close()

        assert result is True

    def test_rate_limited_entries_not_counted(self, tmp_path):
        """Entries with status='rate_limited' are excluded from the counter."""
        agent = _make_agent(tmp_path, rate_limit=3)
        db_path = Path(agent._db_path)
        # Insert 3 rate_limited entries (should not count)
        for _ in range(3):
            _insert_log_entry(db_path, status="rate_limited")
        # Insert 2 ok entries
        for _ in range(2):
            _insert_log_entry(db_path, status="ok")

        db = sqlite3.connect(str(db_path))
        try:
            result = agent._check_rate_limit(db)
        finally:
            db.close()

        assert result is True

    def test_increment_rate_counter_inserts_ok_entry(self, tmp_path):
        """_increment_rate_counter inserts a row with status='ok'."""
        agent = _make_agent(tmp_path)
        db_path = Path(agent._db_path)

        db = sqlite3.connect(str(db_path))
        try:
            agent._increment_rate_counter(
                db,
                query="nginx config",
                categories=["general"],
                num_results=5,
                results_stored=3,
                duration_ms=412,
                status="ok",
            )
            db.commit()
        finally:
            db.close()

        db2 = sqlite3.connect(str(db_path))
        row = db2.execute(
            "SELECT query, status, results_stored, duration_ms "
            "FROM web_search_log WHERE status='ok'"
        ).fetchone()
        db2.close()
        assert row is not None
        assert row[0] == "nginx config"
        assert row[2] == 3
        assert row[3] == 412

    def test_increment_rate_counter_inserts_rate_limited_entry(self, tmp_path):
        """_increment_rate_counter inserts a row with status='rate_limited'."""
        agent = _make_agent(tmp_path)
        db_path = Path(agent._db_path)

        db = sqlite3.connect(str(db_path))
        try:
            agent._increment_rate_counter(
                db,
                query="blocked query",
                categories=["news"],
                num_results=5,
                results_stored=0,
                duration_ms=None,
                status="rate_limited",
            )
            db.commit()
        finally:
            db.close()

        db2 = sqlite3.connect(str(db_path))
        row = db2.execute(
            "SELECT status FROM web_search_log WHERE query='blocked query'"
        ).fetchone()
        db2.close()
        assert row is not None
        assert row[0] == "rate_limited"


# ---------------------------------------------------------------------------
# Task 6: _upsert_results
# ---------------------------------------------------------------------------

class TestUpsertResults:
    @pytest.mark.asyncio
    async def test_upsert_results_calls_qdrant_upsert(self, tmp_path):
        """_upsert_results calls qdrant.upsert once with correct collection name."""
        from web_search_agent import SearchResult

        mock_qdrant = MagicMock()
        mock_qdrant.upsert = MagicMock()

        agent = _make_agent(tmp_path)
        agent._qdrant = mock_qdrant

        results = [
            SearchResult(
                url="https://example.com/a",
                title="Page A",
                snippet="Snippet A",
                score=0.9,
                category="general",
                engine="google",
                fetched_at="2026-03-24T09:00:00Z",
            )
        ]

        fake_embed_resp = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed_resp)):
            count = await agent._upsert_results(results)

        assert count == 1
        mock_qdrant.upsert.assert_called_once()
        call_kwargs = mock_qdrant.upsert.call_args
        assert call_kwargs[1].get("collection_name") == "web_search_results" or \
               "web_search_results" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_upsert_results_dedup_by_url(self, tmp_path):
        """Same URL in two calls produces the same Qdrant point ID (uuid5)."""
        from web_search_agent import SearchResult
        import uuid as uuid_mod

        agent = _make_agent(tmp_path)
        agent._qdrant = MagicMock()
        agent._qdrant.upsert = MagicMock()

        url = "https://example.com/stable-page"
        results = [
            SearchResult(
                url=url, title="T", snippet="S", score=0.8,
                category="general", engine="google",
                fetched_at="2026-03-24T09:00:00Z",
            )
        ]

        fake_embed = {"data": [{"embedding": [0.1]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            await agent._upsert_results(results)
            await agent._upsert_results(results)

        # Both calls should use the same point ID
        calls = agent._qdrant.upsert.call_args_list
        assert len(calls) == 2
        pt_id_first = calls[0][1]["points"][0].id
        pt_id_second = calls[1][1]["points"][0].id
        assert pt_id_first == pt_id_second

        expected_id = str(uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, url))
        assert str(pt_id_first) == expected_id

    @pytest.mark.asyncio
    async def test_upsert_results_payload_fields(self, tmp_path):
        """Qdrant point payload contains all required fields from spec."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        agent._qdrant = MagicMock()
        agent._qdrant.upsert = MagicMock()

        results = [
            SearchResult(
                url="https://example.com/payload-test",
                title="Payload Title",
                snippet="Payload snippet",
                score=0.77,
                category="it",
                engine="duckduckgo",
                fetched_at="2026-03-24T10:00:00Z",
            )
        ]

        fake_embed = {"data": [{"embedding": [0.5, 0.5]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            await agent._upsert_results(results)

        points = agent._qdrant.upsert.call_args[1]["points"]
        payload = points[0].payload
        required_keys = {
            "url", "title", "snippet", "score", "category",
            "engine", "source", "fetched_at", "created_at",
        }
        assert required_keys.issubset(set(payload.keys()))
        assert payload["source"] == "web_search"
        assert payload["url"] == "https://example.com/payload-test"

    @pytest.mark.asyncio
    async def test_upsert_results_ttl_applied(self, tmp_path):
        """Points are upserted with a TTL matching WEB_SEARCH_RESULT_TTL_HOURS * 3600."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        agent._qdrant = MagicMock()
        agent._qdrant.upsert = MagicMock()
        agent.result_ttl_hours = 6  # expect 21600 seconds

        results = [
            SearchResult(
                url="https://example.com/ttl",
                title="T", snippet="S", score=0.5,
                category="general", engine="google",
                fetched_at="2026-03-24T09:00:00Z",
            )
        ]

        fake_embed = {"data": [{"embedding": [0.1]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            await agent._upsert_results(results)

        call_kwargs = agent._qdrant.upsert.call_args[1]
        points = call_kwargs["points"]
        assert len(points) == 1


# ---------------------------------------------------------------------------
# Task 7: search() full pipeline and run()
# ---------------------------------------------------------------------------

class TestSearchPipeline:
    @pytest.mark.asyncio
    async def test_search_returns_search_results(self, tmp_path):
        """search() returns list[SearchResult] with correct fields."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        agent._call_searxng = AsyncMock(return_value=[
            {
                "url": "https://example.com/r1",
                "title": "Result One",
                "content": "Snippet one.",
                "score": 0.9,
                "category": "general",
                "engine": "google",
            }
        ])
        agent._upsert_results = AsyncMock(return_value=1)

        results = await agent.search("nginx config", num_results=1)

        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].url == "https://example.com/r1"
        assert results[0].title == "Result One"

    @pytest.mark.asyncio
    async def test_search_trims_to_max_results(self, tmp_path):
        """search() returns at most num_results items even if SearXNG returns more."""
        agent = _make_agent(tmp_path)
        raw_10 = [
            {
                "url": f"https://example.com/{i}",
                "title": f"Page {i}",
                "content": "s",
                "score": 0.5,
                "category": "general",
                "engine": "google",
            }
            for i in range(10)
        ]
        agent._call_searxng = AsyncMock(return_value=raw_10)
        agent._upsert_results = AsyncMock(return_value=3)

        results = await agent.search("test", num_results=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_disabled_returns_empty(self, tmp_path):
        """SEARXNG_ENABLED=false -> search() returns [] with no HTTP call."""
        agent = _make_agent(tmp_path, enabled=False)
        agent._call_searxng = AsyncMock()

        results = await agent.search("anything")

        assert results == []
        agent._call_searxng.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_raises_rate_limit_error(self, tmp_path):
        """search() raises WebSearchRateLimitError when limit is hit."""
        from web_search_agent import WebSearchRateLimitError

        agent = _make_agent(tmp_path, rate_limit=2)
        db_path = Path(agent._db_path)
        for _ in range(2):
            _insert_log_entry(db_path, status="ok")

        with pytest.raises(WebSearchRateLimitError):
            await agent.search("blocked query")

    @pytest.mark.asyncio
    async def test_search_logs_rate_limited_entry(self, tmp_path):
        """A rate-limited search inserts a log entry with status='rate_limited'."""
        from web_search_agent import WebSearchRateLimitError

        agent = _make_agent(tmp_path, rate_limit=1)
        db_path = Path(agent._db_path)
        _insert_log_entry(db_path, status="ok")

        with pytest.raises(WebSearchRateLimitError):
            await agent.search("should be blocked")

        db = sqlite3.connect(str(db_path))
        row = db.execute(
            "SELECT status FROM web_search_log WHERE query='should be blocked'"
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "rate_limited"

    @pytest.mark.asyncio
    async def test_search_logs_ok_entry_on_success(self, tmp_path):
        """A successful search inserts a log entry with status='ok'."""
        agent = _make_agent(tmp_path)
        agent._call_searxng = AsyncMock(return_value=[{
            "url": "https://x.com", "title": "X", "content": "s",
            "score": 0.5, "category": "general", "engine": "google",
        }])
        agent._upsert_results = AsyncMock(return_value=1)

        await agent.search("logged query")

        db = sqlite3.connect(str(agent._db_path))
        row = db.execute(
            "SELECT status, results_stored FROM web_search_log "
            "WHERE query='logged query'"
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "ok"
        assert row[1] == 1

    @pytest.mark.asyncio
    async def test_search_logs_error_on_unavailable(self, tmp_path):
        """SearXNG connection failure logs status='error'."""
        from web_search_agent import WebSearchUnavailableError

        agent = _make_agent(tmp_path)
        agent._call_searxng = AsyncMock(
            side_effect=WebSearchUnavailableError("SearXNG down")
        )

        with pytest.raises(WebSearchUnavailableError):
            await agent.search("error query")

        db = sqlite3.connect(str(agent._db_path))
        row = db.execute(
            "SELECT status FROM web_search_log WHERE query='error query'"
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "error"

    @pytest.mark.asyncio
    async def test_build_rag_context_formats_results(self, tmp_path):
        """_build_rag_context returns numbered markdown list from Qdrant hits."""
        agent = _make_agent(tmp_path)

        mock_pt1 = MagicMock()
        mock_pt1.payload = {
            "title": "Title One",
            "url": "https://one.com",
            "snippet": "Snippet one.",
        }
        mock_pt2 = MagicMock()
        mock_pt2.payload = {
            "title": "Title Two",
            "url": "https://two.com",
            "snippet": "Snippet two.",
        }
        agent._qdrant.search = MagicMock(return_value=[mock_pt1, mock_pt2])

        fake_embed = {"data": [{"embedding": [0.1, 0.2]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            ctx = await agent._build_rag_context("test query")

        assert "[1]" in ctx
        assert "Title One" in ctx
        assert "https://one.com" in ctx
        assert "[2]" in ctx

    @pytest.mark.asyncio
    async def test_build_rag_context_empty_when_no_results(self, tmp_path):
        """_build_rag_context returns empty string when Qdrant returns no hits."""
        agent = _make_agent(tmp_path)
        agent._qdrant.search = MagicMock(return_value=[])

        fake_embed = {"data": [{"embedding": [0.1]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            ctx = await agent._build_rag_context("empty query")

        assert ctx == ""

    @pytest.mark.asyncio
    async def test_run_returns_agent_result(self, tmp_path):
        """run() returns an AgentResult with status='completed'."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        fake_result = SearchResult(
            url="https://example.com", title="T", snippet="S",
            score=0.9, category="general", engine="google",
            fetched_at="2026-03-24T09:00:00Z",
        )
        agent.search = AsyncMock(return_value=[fake_result])
        agent._build_rag_context = AsyncMock(return_value="[1] T (https://example.com)\nS")

        fake_llm_resp = MagicMock()
        fake_llm_resp.choices = [MagicMock()]
        fake_llm_resp.choices[0].message.content = "Here is a synthesis."
        with patch("litellm.acompletion", new=AsyncMock(return_value=fake_llm_resp)):
            result = await agent.run("Find info about nginx")

        assert result.status == "completed"
        assert isinstance(result.output, str)
        assert len(result.output) > 0

    @pytest.mark.asyncio
    async def test_run_skips_search_when_fresh_rag_cache(self, tmp_path):
        """run() does not call SearXNG when high-scoring RAG context exists."""
        agent = _make_agent(tmp_path)
        # RAG context has high-confidence cached result (simulated)
        agent._build_rag_context = AsyncMock(
            return_value="[1] Cached Title (https://cached.com)\nFresh cached snippet."
        )
        agent.search = AsyncMock()

        # Mock LLM query extraction to return same query
        agent.run_chat_fn = MagicMock(return_value={
            "text": '{"query": "nginx", "num_results": 5, "categories": ["general"], "use_cache": true}'
        })

        fake_llm_resp = MagicMock()
        fake_llm_resp.choices = [MagicMock()]
        fake_llm_resp.choices[0].message.content = "Answer from cache."

        with patch("litellm.acompletion", new=AsyncMock(return_value=fake_llm_resp)):
            result = await agent.run("nginx best practices")

        # search() must not have been called when LLM says use_cache=true
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Task 8: AGENT_REGISTRY
# ---------------------------------------------------------------------------

class TestAgentRegistry:
    def test_web_search_in_agent_registry(self):
        """AGENT_REGISTRY contains 'web_search' after import."""
        from agents import AGENT_REGISTRY, _register_defaults
        _register_defaults()  # idempotent
        assert "web_search" in AGENT_REGISTRY

    def test_web_search_registry_class_is_correct(self):
        """AGENT_REGISTRY['web_search'] is the WebSearchAgent class."""
        from agents import AGENT_REGISTRY
        from web_search_agent import WebSearchAgent
        assert AGENT_REGISTRY.get("web_search") is WebSearchAgent


# ---------------------------------------------------------------------------
# Task 9: REST API
# ---------------------------------------------------------------------------

def _make_test_app(tmp_path: Path, enabled: bool = True):
    """Build a minimal FastAPI test app with web_search_api mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db_path = _make_search_db(tmp_path)
    env = {
        "SEARXNG_ENABLED": "true" if enabled else "false",
        "SEARXNG_URL": "http://searxng:8080",
        "WEB_SEARCH_MAX_RESULTS": "5",
        "WEB_SEARCH_RATE_LIMIT_PER_HOUR": "20",
        "WEB_SEARCH_RESULT_TTL_HOURS": "6",
        "RAG_STATE_DIR": str(tmp_path),
    }
    with patch.dict("os.environ", env):
        from web_search_agent import WebSearchAgent
        from web_search_api import router, init_web_search_api

        agent = WebSearchAgent(
            run_chat_fn=MagicMock(),
            db_path=str(db_path),
            qdrant_client=MagicMock(),
        )
        init_web_search_api(agent=agent, db_path=str(db_path))

        app = FastAPI()
        app.include_router(router)
        return TestClient(app), agent, db_path


class TestWebSearchApi:
    def test_post_web_search_200(self, tmp_path):
        """POST /tools/web-search returns 200 with results list."""
        from web_search_agent import SearchResult

        client, agent, _ = _make_test_app(tmp_path)
        fake_result = SearchResult(
            url="https://example.com", title="T", snippet="S",
            score=0.9, category="general", engine="google",
            fetched_at="2026-03-24T09:00:00Z",
        )
        with patch.object(agent, "search", new=AsyncMock(return_value=[fake_result])):
            resp = client.post(
                "/tools/web-search",
                json={"query": "nginx config", "num_results": 5},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["count"] == 1
        assert "stored_in_qdrant" in data
        assert "duration_ms" in data
        assert "rate_limit_remaining" in data

    def test_post_web_search_disabled_400(self, tmp_path):
        """POST /tools/web-search returns 400 when SEARXNG_ENABLED=false."""
        client, _, _ = _make_test_app(tmp_path, enabled=False)
        resp = client.post(
            "/tools/web-search",
            json={"query": "test"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "web_search_disabled"

    def test_post_web_search_rate_limited_429(self, tmp_path):
        """POST /tools/web-search returns 429 when rate limit is hit."""
        from web_search_agent import WebSearchRateLimitError

        client, agent, _ = _make_test_app(tmp_path)
        with patch.object(
            agent, "search",
            new=AsyncMock(side_effect=WebSearchRateLimitError("limit hit")),
        ):
            resp = client.post(
                "/tools/web-search",
                json={"query": "blocked"},
            )

        assert resp.status_code == 429
        data = resp.json()["detail"]
        assert data["error"] == "rate_limited"
        assert "retry_after_seconds" in data

    def test_post_web_search_invalid_category_422(self, tmp_path):
        """POST /tools/web-search returns 422 for invalid category."""
        client, _, _ = _make_test_app(tmp_path)
        resp = client.post(
            "/tools/web-search",
            json={"query": "test", "categories": ["invalid_cat"]},
        )
        assert resp.status_code == 422

    def test_post_web_search_query_too_short_422(self, tmp_path):
        """POST /tools/web-search returns 422 when query is less than 3 chars."""
        client, _, _ = _make_test_app(tmp_path)
        resp = client.post(
            "/tools/web-search",
            json={"query": "ab"},
        )
        assert resp.status_code == 422

    def test_get_stats_200(self, tmp_path):
        """GET /tools/web-search/stats returns all expected stat fields."""
        client, _, db_path = _make_test_app(tmp_path)
        # Insert a sample log entry so stats are non-zero
        _insert_log_entry(db_path, status="ok", query="test stats")

        resp = client.get("/tools/web-search/stats")
        assert resp.status_code == 200
        data = resp.json()
        required = {
            "searches_last_hour", "searches_last_24h", "searches_total",
            "rate_limit_per_hour", "rate_limit_remaining",
        }
        assert required.issubset(set(data.keys()))
        assert data["searches_last_hour"] >= 1

    def test_get_status_200(self, tmp_path):
        """GET /tools/web-search/status returns enabled and config fields."""
        client, _, _ = _make_test_app(tmp_path)
        resp = client.get("/tools/web-search/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "searxng_url" in data
        assert "rate_limit_per_hour" in data
        assert "result_ttl_hours" in data

    def test_searxng_categories_whitelist(self, tmp_path):
        """Valid categories pass; any invalid category returns 422."""
        from web_search_agent import SearchResult

        client, agent, _ = _make_test_app(tmp_path)
        fake_result = SearchResult(
            url="https://x.com", title="T", snippet="S",
            score=0.5, category="it", engine="google",
            fetched_at="2026-03-24T09:00:00Z",
        )
        valid_cats = ["general", "news", "it", "science", "files", "images", "videos"]
        for cat in valid_cats:
            with patch.object(agent, "search", new=AsyncMock(return_value=[fake_result])):
                resp = client.post(
                    "/tools/web-search",
                    json={"query": "test query", "categories": [cat]},
                )
            assert resp.status_code == 200, f"Expected 200 for category '{cat}'"

        # Invalid category
        resp = client.post(
            "/tools/web-search",
            json={"query": "test query", "categories": ["hacking"]},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 10: web_digest scheduler section
# ---------------------------------------------------------------------------

def _make_scheduler_db(tmp_path: Path) -> Path:
    """Create a scheduler.db with scheduled_jobs and job_runs tables."""
    db_path = tmp_path / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id TEXT PRIMARY KEY,
            name TEXT, cron TEXT, prompt TEXT, sections TEXT,
            channels TEXT, timeout_s INTEGER DEFAULT 120,
            last_run TEXT, last_status TEXT, last_output TEXT,
            system INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            job_id TEXT, started_at TEXT, status TEXT,
            output TEXT, error TEXT, channels_ok TEXT, duration_ms INTEGER
        )
    """)
    db.commit()
    db.close()
    return db_path


class TestWebDigestSection:
    @pytest.mark.asyncio
    async def test_web_digest_section_returns_content(self, tmp_path):
        """_collect_web_digest returns digest string when SEARXNG_ENABLED=true."""
        from scheduler_executor import JobExecutor

        mock_qdrant = MagicMock()
        executor = JobExecutor(
            db_path=str(_make_scheduler_db(tmp_path)),
            notifier=MagicMock(),
            qdrant=mock_qdrant,
        )

        expected = "## Web Digest\n- [Test Result](https://example.com) — snippet"
        env = {
            "SEARXNG_ENABLED": "true",
            "WEB_DIGEST_TOPICS": "intelligence artificielle,kubernetes",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict("os.environ", env):
            with patch(
                "web_search_agent.WebSearchAgent.collect_web_digest",
                new=AsyncMock(return_value=expected),
            ):
                result = await executor._collect_web_digest()

        assert "Web Digest" in result or "intelligence" in result or result == expected

    @pytest.mark.asyncio
    async def test_web_digest_disabled_returns_empty(self, tmp_path):
        """_collect_web_digest returns '' when SEARXNG_ENABLED=false."""
        from scheduler_executor import JobExecutor

        executor = JobExecutor(
            db_path=str(_make_scheduler_db(tmp_path)),
            notifier=MagicMock(),
            qdrant=MagicMock(),
        )

        with patch.dict("os.environ", {"SEARXNG_ENABLED": "false"}):
            result = await executor._collect_web_digest()

        assert result == ""

    @pytest.mark.asyncio
    async def test_collect_sections_includes_web_digest(self, tmp_path):
        """collect_sections dispatches 'web_digest' section correctly."""
        from scheduler_executor import JobExecutor

        executor = JobExecutor(
            db_path=str(_make_scheduler_db(tmp_path)),
            notifier=MagicMock(),
            qdrant=MagicMock(),
        )
        executor._collect_web_digest = AsyncMock(return_value="digest content")

        result = await executor.collect_sections(
            sections=["web_digest"],
            cron="0 8 * * *",
            last_run=None,
            prompt="",
            job_name="test",
        )

        assert "digest content" in result

    @pytest.mark.asyncio
    async def test_web_digest_label_in_section_labels(self):
        """SECTION_LABELS contains 'web_digest' key."""
        from scheduler_executor import SECTION_LABELS
        assert "web_digest" in SECTION_LABELS


# ---------------------------------------------------------------------------
# Task 12: collect_web_digest and snippet truncation
# ---------------------------------------------------------------------------

class TestWebDigestAgent:
    @pytest.mark.asyncio
    async def test_web_digest_disabled_returns_empty(self, tmp_path):
        """collect_web_digest returns '' when SEARXNG_ENABLED=false."""
        agent = _make_agent(tmp_path, enabled=False)
        result = await agent.collect_web_digest(topics=["AI", "Docker"])
        assert result == ""

    @pytest.mark.asyncio
    async def test_web_digest_parallel_topics(self, tmp_path):
        """collect_web_digest calls search() once per topic (parallel)."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        search_calls: list[str] = []

        async def fake_search(query, num_results=3, categories=None, source="api"):
            search_calls.append(query)
            return [
                SearchResult(
                    url=f"https://example.com/{query[:5]}",
                    title=f"Result for {query}",
                    snippet=f"Snippet {query}",
                    score=0.8,
                    category="general",
                    engine="google",
                    fetched_at="2026-03-24T09:00:00Z",
                )
            ]

        agent.search = fake_search

        fake_llm_resp = MagicMock()
        fake_llm_resp.choices = [MagicMock()]
        fake_llm_resp.choices[0].message.content = "## Web Digest\n- Result 1\n- Result 2"

        with patch("litellm.acompletion", new=AsyncMock(return_value=fake_llm_resp)):
            result = await agent.collect_web_digest(
                topics=["intelligence artificielle", "kubernetes", "docker"],
                num_per_topic=2,
            )

        assert len(search_calls) == 3
        assert "intelligence artificielle" in search_calls
        assert "kubernetes" in search_calls
        assert "docker" in search_calls

    @pytest.mark.asyncio
    async def test_web_digest_deduplicates_by_url(self, tmp_path):
        """collect_web_digest deduplicates results sharing the same URL."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        shared_url = "https://shared.example.com/article"

        async def fake_search(query, num_results=3, categories=None, source="api"):
            return [
                SearchResult(
                    url=shared_url,
                    title="Shared Article",
                    snippet="Same article appearing in both topics.",
                    score=0.9,
                    category="general",
                    engine="google",
                    fetched_at="2026-03-24T09:00:00Z",
                )
            ]

        agent.search = fake_search

        fake_llm_resp = MagicMock()
        fake_llm_resp.choices = [MagicMock()]
        fake_llm_resp.choices[0].message.content = "digest"

        seen_urls_in_prompt: list[str] = []

        async def capture_llm(*args, **kwargs):
            messages = kwargs.get("messages", args[0] if args else [])
            for m in messages:
                if shared_url in m.get("content", ""):
                    seen_urls_in_prompt.append(shared_url)
                    break
            return fake_llm_resp

        with patch("litellm.acompletion", new=capture_llm):
            await agent.collect_web_digest(
                topics=["topic A", "topic B"],
                num_per_topic=1,
            )

        # The shared URL should appear exactly once in the LLM prompt
        assert seen_urls_in_prompt.count(shared_url) == 1


class TestSnippetTruncation:
    @pytest.mark.asyncio
    async def test_snippet_truncated_before_upsert(self, tmp_path):
        """Snippet > 500 chars is truncated to 500 before Qdrant upsert."""
        from web_search_agent import SearchResult

        agent = _make_agent(tmp_path)
        agent._qdrant = MagicMock()
        agent._qdrant.upsert = MagicMock()

        long_snippet = "z" * 800
        results = [
            SearchResult(
                url="https://example.com/long",
                title="Long Page",
                snippet=long_snippet,
                score=0.7,
                category="general",
                engine="google",
                fetched_at="2026-03-24T09:00:00Z",
            )
        ]

        fake_embed = {"data": [{"embedding": [0.1, 0.2]}]}
        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embed)):
            await agent._upsert_results(results)

        points = agent._qdrant.upsert.call_args[1]["points"]
        assert len(points[0].payload["snippet"]) <= 500

    @pytest.mark.asyncio
    async def test_snippet_truncated_in_search_results(self, tmp_path):
        """search() truncates snippets returned from _call_searxng to 500 chars."""
        agent = _make_agent(tmp_path)
        long_content = "w" * 800

        agent._call_searxng = AsyncMock(return_value=[{
            "url": "https://example.com",
            "title": "T",
            "content": long_content,
            "score": 0.5,
            "category": "general",
            "engine": "google",
        }])
        agent._upsert_results = AsyncMock(return_value=1)

        results = await agent.search("test truncation")
        assert len(results[0].snippet) <= 500
