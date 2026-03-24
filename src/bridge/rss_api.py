"""RSS API — FastAPI router for /api/rss/*."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("rag-bridge.rss_api")

router = APIRouter(prefix="/api/rss", tags=["rss"])

# Injected at startup by app.py
_ingestor: Any = None


def init_rss_api(ingestor: Any) -> None:
    global _ingestor
    _ingestor = ingestor


def _get_ingestor() -> Any:
    if _ingestor is None:
        raise HTTPException(status_code=503, detail="RssIngestor not initialised")
    return _ingestor


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class FeedCreate(BaseModel):
    url: str
    name: str = ""
    category: str = "general"
    refresh_interval_min: int = Field(default=60, ge=15, le=1440)


class FeedUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    refresh_interval_min: Optional[int] = Field(default=None, ge=15, le=1440)
    enabled: Optional[bool] = None


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/feeds")
def list_feeds() -> list[dict]:
    ingestor = _get_ingestor()
    return ingestor.list_feeds()


@router.get("/feeds/{feed_id}")
def get_feed(feed_id: str) -> dict:
    ingestor = _get_ingestor()
    feed = ingestor.get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


@router.post("/feeds", status_code=201)
def add_feed(body: FeedCreate) -> dict:
    ingestor = _get_ingestor()

    # URL validation
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    import os
    if not os.getenv("RSS_ALLOW_LOCAL_URLS", "false").lower() in ("1", "true", "yes"):
        _check_not_local_url(body.url)

    try:
        result = ingestor.add_feed(
            url=body.url,
            category=body.category,
            refresh_interval_min=body.refresh_interval_min,
            name=body.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail="Feed URL already exists")
        raise HTTPException(status_code=500, detail=str(e))

    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.put("/feeds/{feed_id}")
def update_feed(feed_id: str, body: FeedUpdate) -> dict:
    ingestor = _get_ingestor()
    feed = ingestor.get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    updates = body.model_dump(exclude_none=True)
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0

    result = ingestor.update_feed(feed_id, **updates)
    return result or {}


@router.patch("/feeds/{feed_id}")
def patch_feed(feed_id: str, body: FeedUpdate) -> dict:
    return update_feed(feed_id, body)


@router.delete("/feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: str) -> None:
    ingestor = _get_ingestor()
    deleted = ingestor.delete_feed(feed_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Feed not found")


@router.post("/feeds/{feed_id}/toggle")
async def toggle_feed(feed_id: str) -> dict:
    ingestor = _get_ingestor()
    feed = ingestor.get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    new_state = not bool(feed.get("enabled", 1))
    ingestor.enable_feed(feed_id, new_state)
    return {"feed_id": feed_id, "enabled": new_state}


@router.post("/feeds/{feed_id}/sync")
async def sync_feed(feed_id: str) -> dict:
    ingestor = _get_ingestor()
    feed = ingestor.get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    result = await ingestor.sync_feed(feed_id)
    return {"synced": result.get("synced", 0), "new": result.get("new", 0)}


@router.get("/feeds/{feed_id}/articles")
def get_feed_articles(feed_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
    ingestor = _get_ingestor()
    feed = ingestor.get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    db = ingestor._connect()
    try:
        rows = db.execute(
            "SELECT * FROM rss_entries WHERE feed_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (feed_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/sync")
async def sync_all() -> dict:
    ingestor = _get_ingestor()
    result = await ingestor.sync_all_feeds()
    return result


@router.get("/status")
def get_status() -> dict:
    ingestor = _get_ingestor()
    return ingestor.get_status()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_LOCAL_BLACKLIST = ("localhost", "127.", "10.", "192.168.", "::1", "0.0.0.0")


def _check_not_local_url(url: str) -> None:
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    for pat in _LOCAL_BLACKLIST:
        if host == pat or host.startswith(pat):
            raise HTTPException(
                status_code=422,
                detail=f"Local/private URLs are not allowed (host: {host})",
            )
