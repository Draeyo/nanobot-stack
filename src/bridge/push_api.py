"""push_api — REST endpoints for Web Push subscription management."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.push_api")

PUSH_ENABLED = os.getenv("PUSH_ENABLED", "false").lower() == "true"

router = APIRouter(prefix="/api/push", tags=["push"])

# Lazy singleton — initialized in app.py startup
_push_manager = None


def init_push_api(push_manager) -> None:
    """Called from app.py startup to inject the PushNotificationManager instance."""
    global _push_manager  # pylint: disable=global-statement
    _push_manager = push_manager


def _require_push():
    if not PUSH_ENABLED or _push_manager is None:
        raise HTTPException(status_code=503, detail="Push notifications not enabled")
    return _push_manager


# ---------------------------------------------------------------------------
# Task 6: GET /api/push/vapid-public-key
# ---------------------------------------------------------------------------
@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key for client-side push subscription."""
    mgr = _require_push()
    return {"vapid_public_key": mgr.get_vapid_public_key()}


# ---------------------------------------------------------------------------
# Task 7: POST /api/push/subscribe
# ---------------------------------------------------------------------------
class SubscribeRequest(BaseModel):
    """Request body for push subscription registration."""
    endpoint: str
    p256dh: str
    auth: str


@router.post("/subscribe", status_code=201)
async def subscribe_push(body: SubscribeRequest):
    """Store a browser push subscription. Upserts on duplicate endpoint."""
    mgr = _require_push()
    # Check if endpoint already exists — if so, treat as update (200)
    existing_subs = mgr.get_all_subscriptions()
    is_update = any(s["endpoint"] == body.endpoint for s in existing_subs)

    sub_id = mgr.subscribe(body.endpoint, body.p256dh, body.auth)

    if is_update:
        return JSONResponse(
            status_code=200,
            content={"id": sub_id, "message": "Souscription mise \u00e0 jour"},
        )
    return {"id": sub_id, "message": "Souscription enregistr\u00e9e"}


# ---------------------------------------------------------------------------
# Task 8: DELETE /api/push/unsubscribe
# ---------------------------------------------------------------------------
class UnsubscribeRequest(BaseModel):
    """Request body for push unsubscription."""
    endpoint: str


@router.delete("/unsubscribe")
async def unsubscribe_push(body: UnsubscribeRequest):
    """Remove a browser push subscription by endpoint."""
    mgr = _require_push()
    removed = mgr.unsubscribe(body.endpoint)
    if not removed:
        raise HTTPException(status_code=404, detail="Souscription introuvable")
    return {"message": "Souscription supprim\u00e9e"}


# ---------------------------------------------------------------------------
# Task 9: POST /api/push/test
# ---------------------------------------------------------------------------
@router.post("/test")
async def test_push():
    """Send a test push notification to all subscriptions."""
    mgr = _require_push()
    result = mgr.send_to_all(
        title="Nanobot \u2014 Test",
        body="Notification de test depuis le serveur Nanobot.",
        url="/",
    )
    return result
