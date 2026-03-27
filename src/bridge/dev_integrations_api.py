"""dev_integrations_api — REST endpoints for GitHub and Obsidian integrations."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.dev_integrations_api")

router = APIRouter(tags=["dev-integrations"])

_manager: Any = None


def init_dev_integrations_api(manager: Any) -> None:
    """Inject the DevIntegrationManager instance used by all endpoints."""
    global _manager  # pylint: disable=global-statement
    _manager = manager


class GitHubSyncRequest(BaseModel):
    """Request body for POST /github/sync."""

    repos: Optional[List[str]] = None


@router.get("/status")
async def get_status() -> dict:
    """Return combined GitHub + Obsidian integration status."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="dev integrations not initialized")
    return _manager.get_status()


@router.post("/github/sync")
async def trigger_github_sync(body: GitHubSyncRequest) -> dict:
    """Trigger an immediate GitHub sync."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="dev integrations not initialized")
    result = await _manager.sync_github(repos=body.repos)
    if result.get("status") == "disabled":
        raise HTTPException(status_code=503, detail="GitHub integration disabled")
    return result


@router.get("/github/log")
async def get_github_log(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return paginated GitHub sync log."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="dev integrations not initialized")
    return _manager.get_github_sync_log(limit=limit, offset=offset)


@router.get("/obsidian/status")
async def get_obsidian_status() -> dict:
    """Return Obsidian vault status."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="dev integrations not initialized")
    return _manager.get_obsidian_status()


@router.post("/obsidian/sync")
async def trigger_obsidian_sync() -> dict:
    """Trigger an immediate Obsidian vault ingestion."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="dev integrations not initialized")
    return await _manager.obsidian_ingest_vault()
