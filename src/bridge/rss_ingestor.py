"""RssIngestor — fetch, parse, embed, and upsert RSS feeds into Qdrant."""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("rag-bridge.rss_ingestor")

RSS_COLLECTION = "rss_articles"
MAX_TEXT_LEN = 10_000


class RssIngestor:
    """Pipeline: fetch XML → parse → embed → Qdrant upsert with SQLite dedup."""

    def __init__(
        self,
        state_dir: str | pathlib.Path,
        qdrant_client: Any,
        litellm_client: Any = None,
    ) -> None:
        self._state_dir = pathlib.Path(state_dir)
        self._qdrant = qdrant_client
        self._litellm = litellm_client  # unused directly — we import litellm inline

        self._db_path = self._state_dir / "rss.db"
        self._enabled = os.getenv("RSS_ENABLED", "false").lower() in ("1", "true", "yes")
        self._summarize_enabled = os.getenv("RSS_SUMMARIZE_ENABLED", "true").lower() in ("1", "true", "yes")
        self._embed_full_text = os.getenv("RSS_EMBED_FULL_TEXT", "false").lower() in ("1", "true", "yes")
        self._max_articles_digest = int(os.getenv("RSS_MAX_ARTICLES_PER_DIGEST", "10"))
        self._ttl_days = int(os.getenv("RSS_ARTICLE_TTL_DAYS", "30"))
        self._sync_timeout_s = int(os.getenv("RSS_SYNC_TIMEOUT_S", "30"))

    # ------------------------------------------------------------------
    # Internal DB helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Feed CRUD
    # ------------------------------------------------------------------

    def add_feed(self, url: str, category: str = "general",
                 refresh_interval_min: int = 60, name: str = "") -> dict:
        """Add a new RSS feed. Fetches title from feed if name not provided."""
        if not self._enabled:
            return {"error": "RSS_ENABLED is false"}

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL scheme: {url}")

        # Fetch feed title if name not provided
        if not name:
            try:
                import feedparser  # type: ignore[import]
                parsed = feedparser.parse(url)
                name = parsed.feed.get("title", url)
            except Exception:
                name = url

        feed_id = str(uuid.uuid4())
        now = self._now()
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO rss_feeds (id, url, name, category, refresh_interval_min, "
                "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (feed_id, url, name, category, refresh_interval_min, now, now),
            )
            db.commit()
        finally:
            db.close()

        return {
            "id": feed_id,
            "url": url,
            "name": name,
            "category": category,
            "refresh_interval_min": refresh_interval_min,
            "enabled": True,
            "created_at": now,
        }

    def list_feeds(self) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT * FROM rss_feeds ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def get_feed(self, feed_id: str) -> dict | None:
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM rss_feeds WHERE id=?", (feed_id,)).fetchone()
            return dict(row) if row else None
        finally:
            db.close()

    def delete_feed(self, feed_id: str) -> bool:
        db = self._connect()
        try:
            cursor = db.execute("DELETE FROM rss_feeds WHERE id=?", (feed_id,))
            db.execute("DELETE FROM rss_entries WHERE feed_id=?", (feed_id,))
            db.commit()
            return cursor.rowcount > 0
        finally:
            db.close()

    def enable_feed(self, feed_id: str, enabled: bool) -> None:
        now = self._now()
        db = self._connect()
        try:
            db.execute(
                "UPDATE rss_feeds SET enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, now, feed_id),
            )
            db.commit()
        finally:
            db.close()

    def update_feed(self, feed_id: str, **kwargs: Any) -> dict | None:
        allowed = {"category", "refresh_interval_min", "enabled", "name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_feed(feed_id)
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [feed_id]
        db = self._connect()
        try:
            db.execute(f"UPDATE rss_feeds SET {set_clause} WHERE id=?", values)
            db.commit()
        finally:
            db.close()
        return self.get_feed(feed_id)

    def get_total_entries(self) -> int:
        db = self._connect()
        try:
            row = db.execute("SELECT COUNT(*) FROM rss_entries").fetchone()
            return row[0] if row else 0
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Feed fetching + dedup
    # ------------------------------------------------------------------

    async def fetch_feed(self, feed_id: str) -> list[dict]:
        """Fetch feed via feedparser, return new entries not yet in rss_entries."""
        feed_row = self.get_feed(feed_id)
        if not feed_row:
            raise ValueError(f"Feed not found: {feed_id}")

        url = feed_row["url"]
        try:
            import feedparser  # type: ignore[import]
            # Use httpx for async fetch, feedparser for parsing
            try:
                import httpx  # type: ignore[import]
                async with httpx.AsyncClient(timeout=self._sync_timeout_s) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    raw = resp.text
                parsed = feedparser.parse(raw)
            except Exception:
                # Fallback: direct feedparser (sync, blocking)
                parsed = await asyncio.to_thread(feedparser.parse, url)
        except Exception as e:
            logger.warning("Failed to fetch feed %s (%s): %s", feed_id, url, e)
            return []

        # Load existing entry_ids for dedup
        db = self._connect()
        try:
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT entry_id FROM rss_entries WHERE feed_id=?", (feed_id,)
                ).fetchall()
            }
        finally:
            db.close()

        new_entries = []
        for entry in parsed.get("entries", []):
            entry_id = entry.get("id") or entry.get("link", "")
            if not entry_id or entry_id in existing:
                continue

            # Parse published date
            published_at = None
            if entry.get("published_parsed"):
                try:
                    from time import mktime
                    dt = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
                    published_at = dt.isoformat()
                except Exception:
                    pass

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            # Truncate to protect memory
            summary = summary[:MAX_TEXT_LEN]

            new_entries.append({
                "entry_id": entry_id,
                "url": entry.get("link", entry_id),
                "title": title,
                "summary": summary,
                "published_at": published_at,
                "feed_id": feed_id,
                "feed_url": url,
                "feed_name": feed_row.get("name", ""),
                "category": feed_row.get("category", "general"),
            })

        return new_entries

    def _save_entries(self, entries: list[dict]) -> None:
        now = self._now()
        db = self._connect()
        try:
            for e in entries:
                row_id = str(uuid.uuid4())
                try:
                    db.execute(
                        "INSERT OR IGNORE INTO rss_entries "
                        "(id, feed_id, entry_id, url, title, published_at, embedded, summarized, created_at) "
                        "VALUES (?,?,?,?,?,?,0,0,?)",
                        (row_id, e["feed_id"], e["entry_id"], e["url"],
                         e["title"], e.get("published_at"), now),
                    )
                except sqlite3.IntegrityError:
                    pass
            db.commit()
        finally:
            db.close()

    def _mark_embedded(self, entry_ids: list[str]) -> None:
        if not entry_ids:
            return
        db = self._connect()
        try:
            db.executemany(
                "UPDATE rss_entries SET embedded=1, summarized=1 WHERE entry_id=?",
                [(eid,) for eid in entry_ids],
            )
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Embedding + Qdrant upsert
    # ------------------------------------------------------------------

    async def embed_and_upsert(self, entries: list[dict]) -> int:
        """Embed title+summary and upsert to Qdrant rss_articles. Returns count upserted."""
        if not entries:
            return 0
        if not self._qdrant:
            logger.warning("Qdrant not available — skipping embed_and_upsert")
            return 0

        try:
            import litellm  # type: ignore[import]
            from qdrant_client.models import PointStruct  # type: ignore[import]

            texts = []
            for e in entries:
                if self._embed_full_text:
                    text = f"{e['title']}. {e.get('summary', '')}"
                else:
                    text = f"{e['title']}. {e.get('summary', '')[:500]}"
                texts.append(text[:MAX_TEXT_LEN])

            # Embed in batches of 50
            batch_size = 50
            all_vectors: list[list[float]] = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i: i + batch_size]
                resp = await litellm.aembedding(model="text-embedding-3-small", input=batch)
                all_vectors.extend([item["embedding"] for item in resp["data"]])

            ttl_seconds = self._ttl_days * 86400
            points = []
            for entry, vector in zip(entries, all_vectors):
                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, entry["url"]))
                payload = {
                    "article_id": article_id,
                    "feed_id": entry["feed_id"],
                    "feed_url": entry.get("feed_url", ""),
                    "feed_name": entry.get("feed_name", ""),
                    "title": entry["title"],
                    "url": entry["url"],
                    "summary": entry.get("llm_summary") or entry.get("summary", ""),
                    "category": entry.get("category", "general"),
                    "published_at": entry.get("published_at", ""),
                    "synced_at": self._now(),
                    "source": "rss",
                    "tags": ["rss", entry.get("category", "general")],
                    "text": texts[entries.index(entry)],
                }
                # Include TTL in payload for Qdrant TTL filter
                payload["ttl_expires_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
                ).isoformat()

                points.append(PointStruct(id=article_id, vector=vector, payload=payload))

            # Ensure collection exists
            try:
                self._qdrant.get_collection(RSS_COLLECTION)
            except Exception:
                from qdrant_client.models import VectorParams, Distance  # type: ignore[import]
                self._qdrant.recreate_collection(
                    collection_name=RSS_COLLECTION,
                    vectors_config=VectorParams(size=len(all_vectors[0]), distance=Distance.COSINE),
                )

            self._qdrant.upsert(collection_name=RSS_COLLECTION, points=points)
            self._mark_embedded([e["entry_id"] for e in entries])
            return len(points)

        except Exception as e:
            logger.exception("embed_and_upsert failed: %s", e)
            return 0

    # ------------------------------------------------------------------
    # LLM summarisation
    # ------------------------------------------------------------------

    async def summarize_entry(self, entry: dict) -> str:
        """Generate a short LLM summary (max ~100 tokens) for an entry."""
        try:
            import litellm  # type: ignore[import]
            title = entry.get("title", "")
            snippet = entry.get("summary", "")[:1000]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant. Résume cet article RSS en 1-2 phrases concises "
                        "en français. Maximum 100 tokens."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Titre: {title}\n\nContenu: {snippet}",
                },
            ]
            resp = await litellm.acompletion(
                model="claude-haiku-3-5", messages=messages, max_tokens=100
            )
            return resp.choices[0].message.content or snippet
        except Exception as e:
            logger.warning("summarize_entry failed: %s", e)
            return entry.get("summary", "")

    # ------------------------------------------------------------------
    # Sync (single feed + all feeds)
    # ------------------------------------------------------------------

    async def sync_feed(self, feed_id: str) -> dict:
        """Fetch, optionally summarize, embed, and upsert one feed. Returns stats dict."""
        if not self._enabled:
            return {"feed_id": feed_id, "synced": 0, "new": 0, "errors": 0}

        new_entries = await self.fetch_feed(feed_id)
        if not new_entries:
            self._update_feed_status(feed_id, "ok", 0)
            return {"feed_id": feed_id, "synced": 0, "new": 0, "errors": 0}

        # Save to SQLite first (for dedup on restart)
        self._save_entries(new_entries)

        errors = 0
        processed = []
        for entry in new_entries:
            try:
                if self._summarize_enabled:
                    entry["llm_summary"] = await self.summarize_entry(entry)
                processed.append(entry)
            except Exception as e:
                logger.warning("Error processing entry %s: %s", entry.get("entry_id"), e)
                errors += 1
                processed.append(entry)

        upserted = await self.embed_and_upsert(processed)
        self._update_feed_status(feed_id, "ok", len(new_entries))

        return {
            "feed_id": feed_id,
            "synced": len(new_entries),
            "new": upserted,
            "errors": errors,
        }

    def _update_feed_status(self, feed_id: str, status: str, count_new: int) -> None:
        now = self._now()
        db = self._connect()
        try:
            db.execute(
                "UPDATE rss_feeds SET last_fetched=?, last_status=?, "
                "article_count=article_count+?, updated_at=? WHERE id=?",
                (now, status, count_new, now, feed_id),
            )
            db.commit()
        finally:
            db.close()

    async def sync_all_feeds(self) -> dict:
        """Sync all enabled feeds that are due for refresh. Returns aggregate stats."""
        if not self._enabled:
            return {"feeds_synced": 0, "new_articles": 0}

        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM rss_feeds WHERE enabled=1"
            ).fetchall()
            feeds = [dict(r) for r in rows]
        finally:
            db.close()

        now = datetime.now(timezone.utc)
        due_feeds = []
        for feed in feeds:
            if not feed.get("last_fetched"):
                due_feeds.append(feed)
                continue
            try:
                last = datetime.fromisoformat(feed["last_fetched"])
                elapsed_min = (now - last).total_seconds() / 60
                if elapsed_min >= feed["refresh_interval_min"]:
                    due_feeds.append(feed)
            except Exception:
                due_feeds.append(feed)

        if not due_feeds:
            return {"feeds_synced": 0, "new_articles": 0}

        results = await asyncio.gather(
            *[self.sync_feed(f["id"]) for f in due_feeds],
            return_exceptions=True,
        )

        feeds_synced = 0
        new_articles = 0
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Feed sync raised exception: %s", r)
                continue
            feeds_synced += 1
            new_articles += r.get("new", 0)

        logger.info("sync_all_feeds: %d feeds synced, %d new articles", feeds_synced, new_articles)
        return {"feeds_synced": feeds_synced, "new_articles": new_articles}

    # ------------------------------------------------------------------
    # Digest collection (for briefing section rss_digest)
    # ------------------------------------------------------------------

    async def collect_digest(
        self,
        since_hours: int = 24,
        categories: list[str] | None = None,
    ) -> str:
        """Query Qdrant for recent articles grouped by category, return markdown digest."""
        if not self._enabled:
            return ""
        if not self._qdrant:
            return ""

        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()

            must_conditions: list[dict] = [
                {"key": "published_at", "range": {"gte": since}}
            ]
            if categories:
                must_conditions.append(
                    {"key": "category", "match": {"any": categories}}
                )

            results = self._qdrant.scroll(
                collection_name=RSS_COLLECTION,
                scroll_filter={"must": must_conditions},
                limit=self._max_articles_digest * 3,  # over-fetch, then trim per category
            )
            points = results[0] if results else []

            if not points:
                return ""

            # Group by category
            by_category: dict[str, list[dict]] = {}
            for p in points:
                cat = p.payload.get("category", "general")
                by_category.setdefault(cat, []).append(p.payload)

            # Sort each category by published_at desc and limit
            lines_per_cat = max(1, self._max_articles_digest // max(len(by_category), 1))
            parts = []
            for cat, articles in sorted(by_category.items()):
                articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
                articles = articles[:lines_per_cat]
                section_lines = [f"## {cat.capitalize()}"]
                for a in articles:
                    title = a.get("title", "")
                    url = a.get("url", "")
                    summary = a.get("summary", "")
                    if summary:
                        section_lines.append(f"- [{title}]({url}) — {summary}")
                    else:
                        section_lines.append(f"- [{title}]({url})")
                parts.append("\n".join(section_lines))

            return "\n\n".join(parts)

        except Exception as e:
            logger.exception("collect_digest failed: %s", e)
            return f"rss_digest error: {e}"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        feeds = self.list_feeds()
        total_entries = self.get_total_entries()
        last_sync = None
        for f in feeds:
            lf = f.get("last_fetched")
            if lf and (last_sync is None or lf > last_sync):
                last_sync = lf

        rss_articles_count = 0
        if self._qdrant:
            try:
                info = self._qdrant.get_collection(RSS_COLLECTION)
                rss_articles_count = info.points_count or 0
            except Exception:
                pass

        return {
            "enabled": self._enabled,
            "total_feeds": len(feeds),
            "total_entries": total_entries,
            "last_sync": last_sync,
            "rss_articles_count": rss_articles_count,
        }
