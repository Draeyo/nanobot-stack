"""Tests for RssIngestor — migration, feed CRUD, fetch, embed, sync, digest."""
# pylint: disable=protected-access
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_rss_db(tmp_path: Path) -> Path:
    """Create a minimal rss.db matching the 013_rss migration."""
    db_path = tmp_path / "rss.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS rss_feeds (
            id                      TEXT PRIMARY KEY,
            url                     TEXT NOT NULL UNIQUE,
            name                    TEXT NOT NULL,
            category                TEXT NOT NULL DEFAULT 'general',
            refresh_interval_min    INTEGER NOT NULL DEFAULT 60,
            last_fetched            TEXT,
            last_status             TEXT,
            article_count           INTEGER DEFAULT 0,
            enabled                 INTEGER NOT NULL DEFAULT 1,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS rss_entries (
            id          TEXT PRIMARY KEY,
            feed_id     TEXT NOT NULL,
            entry_id    TEXT NOT NULL UNIQUE,
            url         TEXT NOT NULL,
            title       TEXT NOT NULL,
            published_at TEXT,
            embedded    INTEGER NOT NULL DEFAULT 0,
            summarized  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()
    return db_path


def _make_ingestor(tmp_path: Path, qdrant=None, enabled: bool = True):
    """Instantiate RssIngestor with patched env."""
    _make_rss_db(tmp_path)
    with patch.dict("os.environ", {"RSS_ENABLED": "true" if enabled else "false"}):
        from rss_ingestor import RssIngestor
        ingestor = RssIngestor(state_dir=tmp_path, qdrant_client=qdrant or MagicMock())
    return ingestor


def _fake_feedparser_result(entries: list[dict]) -> MagicMock:
    """Build a feedparser-like parsed object."""
    parsed = MagicMock()
    parsed.feed.get = lambda key, default="": {"title": "Test Feed"}.get(key, default)
    entry_mocks = []
    for e in entries:
        em = MagicMock()
        em.get = lambda k, d=None, _e=e: _e.get(k, d)
        em.__getitem__ = lambda self, k, _e=e: _e[k]
        # Expose .id and .link as attributes
        em.id = e.get("id", e.get("link", ""))
        em.link = e.get("link", "")
        em.title = e.get("title", "")
        em.summary = e.get("summary", "")
        em.published_parsed = None
        entry_mocks.append(em)
    parsed.get = lambda k, d=None: entry_mocks if k == "entries" else d
    parsed.__getitem__ = lambda self, k: entry_mocks if k == "entries" else []
    return parsed


# ---------------------------------------------------------------------------
# Test: migration creates tables
# ---------------------------------------------------------------------------

class TestMigrationCreatesTables:
    def test_migration_creates_tables(self, tmp_path):
        """013_rss.migrate() should create rss_feeds and rss_entries in rss.db."""
        import importlib.util, os
        migration_path = (
            Path(__file__).parent.parent / "migrations" / "013_rss.py"
        )
        spec = importlib.util.spec_from_file_location("migration_013", migration_path)
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"RAG_STATE_DIR": str(tmp_path)}):
            # Patch STATE_DIR inside the module after load
            spec.loader.exec_module(mod)
            mod.STATE_DIR = tmp_path
            mod.migrate({})

        db_path = tmp_path / "rss.db"
        assert db_path.exists()
        db = sqlite3.connect(str(db_path))
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        db.close()
        assert "rss_feeds" in tables
        assert "rss_entries" in tables

    def test_check_returns_true_when_tables_exist(self, tmp_path):
        """013_rss.check() returns True after migrate()."""
        import importlib.util
        migration_path = (
            Path(__file__).parent.parent / "migrations" / "013_rss.py"
        )
        spec = importlib.util.spec_from_file_location("migration_013_b", migration_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.STATE_DIR = tmp_path
        mod.migrate({})
        assert mod.check({}) is True

    def test_check_returns_false_when_db_missing(self, tmp_path):
        import importlib.util
        migration_path = (
            Path(__file__).parent.parent / "migrations" / "013_rss.py"
        )
        spec = importlib.util.spec_from_file_location("migration_013_c", migration_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.STATE_DIR = tmp_path / "nonexistent"
        assert mod.check({}) is False


# ---------------------------------------------------------------------------
# Test: add_feed
# ---------------------------------------------------------------------------

class TestAddFeed:
    def test_add_feed_mock(self, tmp_path):
        """add_feed saves feed to DB and returns a dict with an id."""
        fake_parsed = MagicMock()
        fake_parsed.feed.get = lambda k, d="": {"title": "Le Monde Tech"}.get(k, d)

        with patch("feedparser.parse", return_value=fake_parsed):
            ingestor = _make_ingestor(tmp_path)
            result = ingestor.add_feed(
                url="https://example.com/rss",
                category="tech",
                refresh_interval_min=30,
            )

        assert "id" in result
        assert result["url"] == "https://example.com/rss"
        assert result["category"] == "tech"

        # Verify persisted in DB
        db = sqlite3.connect(str(tmp_path / "rss.db"))
        row = db.execute("SELECT url, category FROM rss_feeds WHERE id=?", (result["id"],)).fetchone()
        db.close()
        assert row is not None
        assert row[0] == "https://example.com/rss"
        assert row[1] == "tech"

    def test_add_feed_duplicate_url(self, tmp_path):
        """Adding the same URL twice raises an error."""
        fake_parsed = MagicMock()
        fake_parsed.feed.get = lambda k, d="": d

        with patch("feedparser.parse", return_value=fake_parsed):
            ingestor = _make_ingestor(tmp_path)
            ingestor.add_feed(url="https://duplicate.com/rss", name="Feed1")
            with pytest.raises(Exception):
                ingestor.add_feed(url="https://duplicate.com/rss", name="Feed2")

    def test_add_feed_disabled_returns_error(self, tmp_path):
        """When RSS_ENABLED=false, add_feed returns {error: ...}."""
        ingestor = _make_ingestor(tmp_path, enabled=False)
        result = ingestor.add_feed(url="https://example.com/rss")
        assert "error" in result


# ---------------------------------------------------------------------------
# Test: fetch_feed (new entries + dedup)
# ---------------------------------------------------------------------------

class TestFetchFeed:
    @pytest.mark.asyncio
    async def test_fetch_feed_new_entries(self, tmp_path):
        """fetch_feed returns new entries not yet in rss_entries."""
        fake_parsed = MagicMock()
        fake_parsed.feed.get = lambda k, d="": d
        fake_entry = MagicMock()
        fake_entry.get = lambda k, d=None: {
            "id": "https://example.com/article/1",
            "link": "https://example.com/article/1",
            "title": "Test Article",
            "summary": "A brief summary.",
        }.get(k, d)
        fake_entry.published_parsed = None
        fake_parsed.get = lambda k, d=None: [fake_entry] if k == "entries" else d

        with patch("feedparser.parse", return_value=fake_parsed):
            ingestor = _make_ingestor(tmp_path)
            # Insert a feed manually
            import uuid
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            feed_id = str(uuid.uuid4())
            db = ingestor._connect()
            db.execute(
                "INSERT INTO rss_feeds (id, url, name, category, refresh_interval_min, "
                "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (feed_id, "https://example.com/rss", "Example", "tech", 60, now, now)
            )
            db.commit()
            db.close()

            # Patch httpx to raise so feedparser fallback is used
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("no httpx"))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_parsed)):
                    entries = await ingestor.fetch_feed(feed_id)

        assert len(entries) == 1
        assert entries[0]["title"] == "Test Article"
        assert entries[0]["entry_id"] == "https://example.com/article/1"

    @pytest.mark.asyncio
    async def test_fetch_feed_dedup(self, tmp_path):
        """Entry URL already in rss_entries → not returned."""
        import uuid
        from datetime import datetime, timezone

        ingestor = _make_ingestor(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        feed_id = str(uuid.uuid4())

        db = ingestor._connect()
        db.execute(
            "INSERT INTO rss_feeds (id, url, name, category, refresh_interval_min, "
            "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
            (feed_id, "https://feed.com/rss", "Feed", "general", 60, now, now)
        )
        existing_entry_id = "https://feed.com/article/existing"
        db.execute(
            "INSERT INTO rss_entries (id, feed_id, entry_id, url, title, embedded, summarized, created_at) "
            "VALUES (?,?,?,?,?,0,0,?)",
            (str(uuid.uuid4()), feed_id, existing_entry_id,
             "https://feed.com/article/existing", "Old Article", now)
        )
        db.commit()
        db.close()

        fake_entry = MagicMock()
        fake_entry.get = lambda k, d=None: {
            "id": existing_entry_id,
            "link": existing_entry_id,
            "title": "Old Article",
            "summary": "",
        }.get(k, d)
        fake_entry.published_parsed = None
        fake_parsed = MagicMock()
        fake_parsed.get = lambda k, d=None: [fake_entry] if k == "entries" else d

        with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_parsed)):
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("no httpx"))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                entries = await ingestor.fetch_feed(feed_id)

        assert entries == []


# ---------------------------------------------------------------------------
# Test: embed_and_upsert
# ---------------------------------------------------------------------------

class TestEmbedAndUpsert:
    @pytest.mark.asyncio
    async def test_embed_and_upsert(self, tmp_path):
        """embed_and_upsert calls Qdrant upsert with correct payload fields."""
        import uuid

        mock_qdrant = MagicMock()
        mock_qdrant.get_collection = MagicMock(return_value=MagicMock(points_count=0))
        mock_qdrant.upsert = MagicMock()

        fake_embedding_resp = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }

        entries = [{
            "entry_id": "https://example.com/a1",
            "feed_id": str(uuid.uuid4()),
            "feed_url": "https://example.com/rss",
            "feed_name": "Example",
            "title": "Article Title",
            "url": "https://example.com/a1",
            "summary": "Short summary.",
            "category": "tech",
            "published_at": "2026-03-24T10:00:00+00:00",
        }]

        ingestor = _make_ingestor(tmp_path, qdrant=mock_qdrant)

        with patch("litellm.aembedding", new=AsyncMock(return_value=fake_embedding_resp)):
            from qdrant_client.models import PointStruct  # noqa: F401
            count = await ingestor.embed_and_upsert(entries)

        assert count == 1
        mock_qdrant.upsert.assert_called_once()
        call_kwargs = mock_qdrant.upsert.call_args
        points = call_kwargs[1].get("points") or call_kwargs[0][1]
        assert len(points) == 1
        payload = points[0].payload
        assert payload["source"] == "rss"
        assert payload["category"] == "tech"
        assert payload["title"] == "Article Title"


# ---------------------------------------------------------------------------
# Test: summarize_entry
# ---------------------------------------------------------------------------

class TestSummarizeEntry:
    @pytest.mark.asyncio
    async def test_summarize_entry_mock(self, tmp_path):
        """summarize_entry calls litellm and returns the content."""
        ingestor = _make_ingestor(tmp_path)

        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = "Résumé en une phrase."

        entry = {
            "title": "Article Test",
            "summary": "Contenu long de l'article...",
        }

        with patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)):
            summary = await ingestor.summarize_entry(entry)

        assert summary == "Résumé en une phrase."

    @pytest.mark.asyncio
    async def test_summarize_entry_fallback_on_error(self, tmp_path):
        """summarize_entry falls back to original summary if LLM fails."""
        ingestor = _make_ingestor(tmp_path)
        entry = {"title": "Test", "summary": "Original summary."}

        with patch("litellm.acompletion", new=AsyncMock(side_effect=Exception("LLM down"))):
            summary = await ingestor.summarize_entry(entry)

        assert summary == "Original summary."


# ---------------------------------------------------------------------------
# Test: sync_all_feeds
# ---------------------------------------------------------------------------

class TestSyncAllFeeds:
    @pytest.mark.asyncio
    async def test_sync_all_feeds(self, tmp_path):
        """sync_all_feeds returns correct aggregated dict."""
        import uuid

        ingestor = _make_ingestor(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        feed_id = str(uuid.uuid4())
        db = ingestor._connect()
        db.execute(
            "INSERT INTO rss_feeds (id, url, name, category, refresh_interval_min, "
            "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
            (feed_id, "https://sync.com/rss", "SyncFeed", "tech", 60, now, now)
        )
        db.commit()
        db.close()

        fake_sync_result = {"feed_id": feed_id, "synced": 3, "new": 2, "errors": 0}
        with patch.object(ingestor, "sync_feed", new=AsyncMock(return_value=fake_sync_result)):
            result = await ingestor.sync_all_feeds()

        assert result["feeds_synced"] == 1
        assert result["new_articles"] == 2

    @pytest.mark.asyncio
    async def test_sync_all_feeds_disabled(self, tmp_path):
        """When RSS_ENABLED=false, sync_all_feeds returns zeros."""
        ingestor = _make_ingestor(tmp_path, enabled=False)
        result = await ingestor.sync_all_feeds()
        assert result == {"feeds_synced": 0, "new_articles": 0}

    @pytest.mark.asyncio
    async def test_sync_all_feeds_exception_in_one_feed(self, tmp_path):
        """Exception in one feed sync does not stop others."""
        import uuid

        ingestor = _make_ingestor(tmp_path)
        now = datetime.now(timezone.utc).isoformat()

        db = ingestor._connect()
        for i in range(2):
            fid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO rss_feeds (id, url, name, category, refresh_interval_min, "
                "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (fid, f"https://feed{i}.com/rss", f"Feed{i}", "tech", 60, now, now)
            )
        db.commit()
        db.close()

        call_count = 0

        async def fake_sync(feed_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Feed exploded")
            return {"feed_id": feed_id, "synced": 1, "new": 1, "errors": 0}

        with patch.object(ingestor, "sync_feed", side_effect=fake_sync):
            result = await ingestor.sync_all_feeds()

        # One succeeded, one raised
        assert result["feeds_synced"] == 1
        assert result["new_articles"] == 1


# ---------------------------------------------------------------------------
# Test: rss_digest section (JobExecutor)
# ---------------------------------------------------------------------------

class TestRssDigestSection:
    @pytest.mark.asyncio
    async def test_rss_digest_section(self, tmp_path, tmp_db):
        """_collect_rss_digest returns formatted markdown from Qdrant scroll."""
        from scheduler_executor import JobExecutor

        mock_qdrant = MagicMock()
        mock_point = MagicMock()
        mock_point.payload = {
            "title": "AI News",
            "url": "https://example.com/ai",
            "category": "tech",
            "summary": "New model released.",
            "published_at": "2026-03-24T09:00:00+00:00",
        }
        mock_qdrant.scroll = MagicMock(return_value=([mock_point], None))

        executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock(), qdrant=mock_qdrant)

        with patch.dict("os.environ", {"RSS_ENABLED": "true", "RAG_STATE_DIR": str(tmp_path)}):
            # Mock RssIngestor.collect_digest to avoid actual DB
            with patch("rss_ingestor.RssIngestor.collect_digest",
                       new=AsyncMock(return_value="## Tech\n- [AI News](https://example.com/ai) — New model released.")):
                result = await executor._collect_rss_digest(since_hours=24)

        assert "Tech" in result
        assert "AI News" in result

    @pytest.mark.asyncio
    async def test_rss_digest_disabled(self, tmp_db):
        """_collect_rss_digest returns empty string when RSS_ENABLED=false."""
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock(), qdrant=MagicMock())

        with patch.dict("os.environ", {"RSS_ENABLED": "false"}):
            result = await executor._collect_rss_digest(since_hours=24)

        assert result == ""


# ---------------------------------------------------------------------------
# Test: disabled flag
# ---------------------------------------------------------------------------

class TestDisabledFlag:
    def test_add_feed_returns_error_when_disabled(self, tmp_path):
        ingestor = _make_ingestor(tmp_path, enabled=False)
        result = ingestor.add_feed(url="https://example.com/rss")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sync_returns_zeros_when_disabled(self, tmp_path):
        ingestor = _make_ingestor(tmp_path, enabled=False)
        result = await ingestor.sync_all_feeds()
        assert result == {"feeds_synced": 0, "new_articles": 0}

    @pytest.mark.asyncio
    async def test_collect_digest_returns_empty_when_disabled(self, tmp_path):
        ingestor = _make_ingestor(tmp_path, enabled=False)
        result = await ingestor.collect_digest()
        assert result == ""
