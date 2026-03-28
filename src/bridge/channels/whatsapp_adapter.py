"""WhatsApp Business Cloud API adapter using webhooks."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from channels import ChannelAdapter

logger = logging.getLogger("rag-bridge.channels.whatsapp")

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")

router = APIRouter(prefix="/webhooks/whatsapp", tags=["channels"])

# Module-level adapter instance — set during start(), read by webhook handler
_adapter_instance = None


class WhatsAppAdapter(ChannelAdapter):
    name = "whatsapp"

    def __init__(self) -> None:
        super().__init__()
        self._token = WHATSAPP_ACCESS_TOKEN
        self._phone_id = WHATSAPP_PHONE_NUMBER_ID

    def is_configured(self) -> bool:
        return bool(self._token and self._phone_id)

    async def start(self) -> None:
        global _adapter_instance
        if not WHATSAPP_APP_SECRET:
            logger.warning("WHATSAPP_APP_SECRET not set — webhook signature verification DISABLED. "
                           "Set it to secure your WhatsApp webhook endpoint.")
        if not WHATSAPP_VERIFY_TOKEN:
            logger.warning("WHATSAPP_VERIFY_TOKEN not set — webhook challenge verification will fail.")
        _adapter_instance = self
        logger.info("WhatsApp adapter ready (webhook-based at /webhooks/whatsapp)")

    async def stop(self) -> None:
        global _adapter_instance
        _adapter_instance = None

    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{self._phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        # WhatsApp limit: 4096 chars
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for chunk in chunks:
                payload = {
                    "messaging_product": "whatsapp",
                    "to": channel_id,
                    "type": "text",
                    "text": {"body": chunk},
                }
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code >= 400:
                    logger.warning("WhatsApp send error %d: %s", r.status_code, r.text[:500])
        return {"ok": True}


def _verify_signature(body: bytes, signature: str) -> bool:
    """Verify webhook payload signature using app secret."""
    if not WHATSAPP_APP_SECRET:
        logger.debug("Signature verification skipped — WHATSAPP_APP_SECRET not configured")
        return True
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    provided = signature.replace("sha256=", "")
    return hmac.compare_digest(expected, provided)


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def verify_webhook(request: Request):
    """Meta webhook verification (challenge-response)."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified")
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403, content="Verification failed")


@router.post("")
async def receive_webhook(request: Request):
    """Receive incoming WhatsApp messages."""
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(body_bytes, signature):
        return Response(status_code=403, content="Invalid signature")

    data = await request.json()

    # Parse the webhook payload
    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        for msg in messages:
            if msg.get("type") != "text":
                continue

            from_number = msg.get("from", "")
            text = msg.get("text", {}).get("body", "")

            if not text or not from_number or not _adapter_instance:
                continue

            response_text = await _adapter_instance.handle_incoming(
                platform_user_id=from_number,
                text=text,
                platform_name="whatsapp",
                extra={"from_number": from_number},
            )

            await _adapter_instance.send_message(from_number, response_text)

    except Exception as exc:
        logger.warning("WhatsApp webhook processing error: %s", exc)

    # Always return 200 to acknowledge receipt
    return Response(status_code=200, content="OK")
