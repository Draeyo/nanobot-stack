"""Web Search API — FastAPI router for /tools/web-search."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from web_search_agent import WebSearchRateLimitError, WebSearchUnavailableError

logger = logging.getLogger("rag-bridge.web_search_api")

router = APIRouter(prefix="/tools/web-search", tags=["web-search"])

# Injected at startup by app.py
_agent: Any = None
_db_path: str = ""

VALID_CATEGORIES = {"general", "news", "it", "science", "files", "images", "videos"}


def init_web_search_api(agent: Any, db_path: str) -> None:
    """Inject dependencies at startup."""
    global _agent, _db_path  # pylint: disable=global-statement
    _agent = agent
    _db_path = db_path


def _get_agent() -> Any:
    if _agent is None:
        raise HTTPException(status_code=503, detail="WebSearchAgent not initialised")
    return _agent


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class WebSearchRequest(BaseModel):
    """Request body for POST /tools/web-search."""

    query: str = Field(..., min_length=3, max_length=500)
    num_results: int = Field(default=5, ge=1, le=20)
    categories: List[str] = Field(default=["general"])

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str]) -> list[str]:
        """Validate that all categories are in the whitelist."""
        invalid = [c for c in v if c not in VALID_CATEGORIES]
        if invalid:
            raise ValueError(
                f"Invalid categories: {invalid}. "
                f"Allowed: {sorted(VALID_CATEGORIES)}"
            )
        return v


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _rate_limit_remaining(db_path: str, rate_limit: int) -> int:
    """Compute remaining searches in the current 1-hour window."""
    try:
        window_start = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        db = sqlite3.connect(db_path)
        try:
            row = db.execute(
                "SELECT COUNT(*) FROM web_search_log "
                "WHERE created_at >= ? AND status != 'rate_limited'",
                (window_start,),
            ).fetchone()
            used = row[0] if row else 0
        finally:
            db.close()
        return max(0, rate_limit - used)
    except Exception:
        return rate_limit


def _get_stats(db_path: str, rate_limit: int) -> dict:
    """Compute search statistics from web_search_log."""
    now = datetime.now(timezone.utc)
    h1 = (now - timedelta(hours=1)).isoformat()
    h24 = (now - timedelta(hours=24)).isoformat()
    try:
        db = sqlite3.connect(db_path)
        try:
            last_hour = db.execute(
                "SELECT COUNT(*) FROM web_search_log "
                "WHERE created_at >= ? AND status != 'rate_limited'",
                (h1,),
            ).fetchone()[0]
            last_24h = db.execute(
                "SELECT COUNT(*) FROM web_search_log "
                "WHERE created_at >= ? AND status NOT IN ('rate_limited','error')",
                (h24,),
            ).fetchone()[0]
            total = db.execute(
                "SELECT COUNT(*) FROM web_search_log WHERE status='ok'"
            ).fetchone()[0]
            last_row = db.execute(
                "SELECT created_at, query FROM web_search_log "
                "WHERE status='ok' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            avg_row = db.execute(
                "SELECT AVG(duration_ms) FROM web_search_log WHERE status='ok'"
            ).fetchone()
        finally:
            db.close()
    except Exception:
        last_hour = last_24h = total = 0
        last_row = None
        avg_row = (None,)

    return {
        "searches_last_hour": last_hour,
        "searches_last_24h": last_24h,
        "searches_total": total,
        "rate_limit_per_hour": rate_limit,
        "rate_limit_remaining": max(0, rate_limit - last_hour),
        "last_search_at": last_row[0] if last_row else None,
        "last_search_query": last_row[1] if last_row else None,
        "avg_duration_ms": int(avg_row[0]) if avg_row and avg_row[0] else 0,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("")
async def post_web_search(body: WebSearchRequest) -> dict:
    """Execute a web search via SearXNG."""
    import time
    agent = _get_agent()

    if not agent.enabled:
        raise HTTPException(
            status_code=400,
            detail={"error": "web_search_disabled",
                    "message": "Set SEARXNG_ENABLED=true to use web search."},
        )

    t0 = time.monotonic()
    try:
        results = await agent.search(
            body.query, body.num_results, body.categories, source="api"
        )
    except WebSearchRateLimitError as exc:
        remaining = _rate_limit_remaining(_db_path, agent.rate_limit)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "retry_after_seconds": 3600,
                "rate_limit_remaining": remaining,
            },
        ) from exc
    except WebSearchUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "searxng_unavailable", "message": str(exc)},
        ) from exc

    duration_ms = int((time.monotonic() - t0) * 1000)
    remaining = _rate_limit_remaining(_db_path, agent.rate_limit)
    stored_in_qdrant = getattr(agent, "_last_upsert_count", len(results))

    return {
        "query": body.query,
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "score": r.score,
                "category": r.category,
                "engine": r.engine,
            }
            for r in results
        ],
        "count": len(results),
        "stored_in_qdrant": stored_in_qdrant,
        "duration_ms": duration_ms,
        "rate_limit_remaining": remaining,
    }


@router.get("/stats")
def get_stats() -> dict:
    """Return search usage statistics."""
    agent = _get_agent()
    return _get_stats(_db_path, agent.rate_limit)


@router.get("/status")
async def get_status() -> dict:
    """Return SearXNG health and configuration status."""
    import httpx  # type: ignore[import]
    agent = _get_agent()
    remaining = _rate_limit_remaining(_db_path, agent.rate_limit) if agent.enabled else None

    reachable = False
    if agent.enabled:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(agent.searxng_url + "/")
                reachable = resp.status_code < 500
        except Exception:
            reachable = False

    return {
        "enabled": agent.enabled,
        "searxng_url": agent.searxng_url if agent.enabled else None,
        "searxng_reachable": reachable,
        "rate_limit_per_hour": agent.rate_limit if agent.enabled else None,
        "rate_limit_remaining": remaining,
        "result_ttl_hours": agent.result_ttl_hours if agent.enabled else None,
    }
