"""REST API for Email/Calendar integration — mounted at /api/email-calendar."""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger("rag-bridge.email_calendar_api")

router = APIRouter(prefix="/api/email-calendar")

_qdrant: Any = None
_verify_token = None


def init_email_calendar_api(qdrant_client: Any, verify_token_dep: Any = None) -> None:
    global _qdrant, _verify_token
    _qdrant = qdrant_client
    _verify_token = verify_token_dep


def _auth():
    return [Depends(_verify_token)] if _verify_token else []


def _make_fetcher():
    from email_calendar import EmailCalendarFetcher
    return EmailCalendarFetcher()


@router.get("/status", dependencies=_auth())
async def get_status():
    """Return enabled flag and last sync status for IMAP and CalDAV/ICS sources."""
    enabled = os.getenv("EMAIL_CALENDAR_ENABLED", "false").lower() == "true"

    fetcher = _make_fetcher()
    sync_status = fetcher.get_sync_status()

    imap_info = sync_status.get("imap", {})
    # CalDAV takes priority over ICS when both are configured
    if os.getenv("CALENDAR_CALDAV_URL"):
        cal_info = sync_status.get("caldav", {})
        cal_account = "caldav"
    else:
        cal_info = sync_status.get("ics", {})
        cal_account = "ics"

    return {
        "enabled": enabled,
        "imap_last_sync": imap_info.get("last_synced"),
        "imap_status": imap_info.get("status"),
        "imap_items": imap_info.get("items_synced", 0),
        f"{cal_account}_last_sync": cal_info.get("last_synced"),
        f"{cal_account}_status": cal_info.get("status"),
        f"{cal_account}_items": cal_info.get("items_synced", 0),
        # Stable keys for clients that don't know which calendar source is active
        "caldav_last_sync": sync_status.get("caldav", {}).get("last_synced"),
        "caldav_status": sync_status.get("caldav", {}).get("status"),
        "caldav_items": sync_status.get("caldav", {}).get("items_synced", 0),
    }


@router.post("/sync", dependencies=_auth())
async def trigger_sync():
    """Manually trigger a sync of emails and calendar events to Qdrant."""
    if not os.getenv("EMAIL_CALENDAR_ENABLED", "false").lower() == "true":
        raise HTTPException(
            status_code=400,
            detail="EMAIL_CALENDAR_ENABLED is not set to true — sync skipped",
        )
    if _qdrant is None:
        raise HTTPException(status_code=503, detail="Qdrant client not available")

    fetcher = _make_fetcher()
    try:
        result = await fetcher.sync_to_qdrant(_qdrant)
    except Exception as exc:
        logger.exception("Manual sync failed")
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}") from exc

    return result
