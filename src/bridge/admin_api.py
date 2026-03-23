"""Admin API — backend endpoints for the admin web UI.

Provides: audit log reader, Qdrant collection browser, and the admin HTML page.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger("rag-bridge.admin")

router = APIRouter(prefix="/admin", tags=["admin"])
_verify_token = None
_qdrant = None

AUDIT_LOG_PATH = pathlib.Path(
    os.getenv("AUDIT_LOG_PATH", "/opt/nanobot-stack/rag-bridge/state/audit.jsonl")
)


def init_admin_api(verify_token_dep=None, qdrant_client=None):
    global _verify_token, _qdrant
    _verify_token = verify_token_dep
    _qdrant = qdrant_client


# ---------------------------------------------------------------------------
# Admin HTML page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def admin_page():
    """Serve the admin SPA."""
    from admin_ui import build_admin_html, ADMIN_ENABLED
    if not ADMIN_ENABLED:
        raise HTTPException(status_code=404, detail="admin UI disabled")
    return build_admin_html()


# ---------------------------------------------------------------------------
# Audit log reader
# ---------------------------------------------------------------------------

@router.get("/audit-log")
def read_audit_log(
    request: Request,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    method: str = Query(""),
    path_filter: str = Query(""),
):
    """Read recent audit log entries with optional filters."""
    if _verify_token:
        _verify_token(request)

    if not AUDIT_LOG_PATH.exists():
        return {"entries": [], "total": 0}

    # Read all lines (JSONL — one JSON object per line)
    entries: list[dict[str, Any]] = []
    try:
        with AUDIT_LOG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Apply filters
                if method and record.get("method", "") != method.upper():
                    continue
                if path_filter and path_filter not in record.get("path", ""):
                    continue

                entries.append(record)
    except Exception as exc:
        logger.warning("Failed to read audit log: %s", exc)
        return {"entries": [], "total": 0, "error": str(exc)}

    # Most recent first
    entries.reverse()
    total = len(entries)
    page = entries[offset : offset + limit]
    return {"entries": page, "total": total, "offset": offset, "limit": limit}


# ---------------------------------------------------------------------------
# Qdrant collection browser
# ---------------------------------------------------------------------------

@router.get("/collections")
def list_collections(request: Request):
    """List all Qdrant collections with point counts."""
    if _verify_token:
        _verify_token(request)
    if not _qdrant:
        raise HTTPException(status_code=503, detail="Qdrant client not available")

    try:
        collections_resp = _qdrant.get_collections()
        result = []
        for c in collections_resp.collections:
            try:
                info = _qdrant.get_collection(c.name)
                result.append({
                    "name": c.name,
                    "points_count": info.points_count,
                    "vectors_count": info.vectors_count,
                    "status": info.status.value if hasattr(info.status, "value") else str(info.status),
                })
            except Exception:
                result.append({"name": c.name, "points_count": None, "status": "error"})
        return {"collections": result}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant error: {exc}")


@router.post("/collections/{name}/scroll")
def scroll_collection(
    name: str,
    request: Request,
    limit: int = Query(20, le=100),
    offset: str = Query(None),
):
    """Scroll through points in a Qdrant collection."""
    if _verify_token:
        _verify_token(request)
    if not _qdrant:
        raise HTTPException(status_code=503, detail="Qdrant client not available")

    try:
        scroll_offset = offset if offset else None
        results, next_offset = _qdrant.scroll(
            collection_name=name,
            limit=limit,
            offset=scroll_offset,
            with_payload=True,
            with_vectors=False,
        )
        points = []
        for p in results:
            payload = dict(p.payload) if p.payload else {}
            # Truncate text fields for UI display
            if "text" in payload and len(str(payload["text"])) > 500:
                payload["text"] = str(payload["text"])[:500] + "..."
            points.append({"id": str(p.id), "payload": payload})
        return {
            "points": points,
            "next_offset": str(next_offset) if next_offset else None,
            "collection": name,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Scroll error: {exc}")
