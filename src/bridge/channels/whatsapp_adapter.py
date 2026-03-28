"""WhatsApp adapter — supports WAHA bridge (QR code) or Meta Cloud API.

WAHA mode (recommended): Uses the user's own phone number via QR code pairing.
  - Requires a WAHA container: docker compose -f docker-compose.whatsapp-web.yml up -d
  - Set WHATSAPP_MODE=waha (default if WHATSAPP_ACCESS_TOKEN is empty)

Cloud mode (legacy): Uses Meta Business Cloud API with webhooks.
  - Set WHATSAPP_MODE=cloud and provide WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from channels import ChannelAdapter

logger = logging.getLogger("rag-bridge.channels.whatsapp")

# --- WAHA bridge config ---
WAHA_URL = os.getenv("WAHA_URL", "http://localhost:3000")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "")

# --- Meta Cloud API config (legacy) ---
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

# Auto-detect mode: waha if no Cloud API token, cloud otherwise
WHATSAPP_MODE = os.getenv("WHATSAPP_MODE", "waha" if not WHATSAPP_ACCESS_TOKEN else "cloud")

router = APIRouter(prefix="/webhooks/whatsapp", tags=["channels"])
_adapter_instance = None


def _waha_headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        h["Authorization"] = f"Bearer {WAHA_API_KEY}"
    return h


class WhatsAppAdapter(ChannelAdapter):
    name = "whatsapp"

    def __init__(self) -> None:
        super().__init__()
        self._mode = WHATSAPP_MODE
        self._session_status = "unknown"
        self._my_number: str = ""

    def is_configured(self) -> bool:
        if self._mode == "waha":
            return bool(WAHA_URL)
        return bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID)

    async def start(self) -> None:
        global _adapter_instance
        _adapter_instance = self
        if self._mode == "waha":
            await self._start_waha()
        else:
            self._start_cloud()

    async def stop(self) -> None:
        global _adapter_instance
        _adapter_instance = None

    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        if self._mode == "waha":
            return await self._send_waha(channel_id, text)
        return await self._send_cloud(channel_id, text)

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "session_status": self._session_status,
            "my_number": self._my_number,
            "waha_url": WAHA_URL if self._mode == "waha" else "",
        }

    # -----------------------------------------------------------------------
    # WAHA mode
    # -----------------------------------------------------------------------

    async def _start_waha(self) -> None:
        logger.info("WhatsApp adapter starting in WAHA mode (bridge: %s)", WAHA_URL)
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(
                    f"{WAHA_URL}/api/sessions",
                    json={"name": WAHA_SESSION, "start": True},
                    headers=_waha_headers(),
                )
                if r.status_code in (200, 201, 409):
                    logger.info("WAHA session '%s' ensured", WAHA_SESSION)
                else:
                    logger.warning("WAHA session create %d: %s", r.status_code, r.text[:200])
            except Exception as exc:
                logger.warning("Cannot reach WAHA at %s: %s — start the container first", WAHA_URL, exc)
                self._session_status = "unreachable"
                return
            await self._refresh_status(client)

        # Background: keep session alive + poll status
        asyncio.create_task(self._keepalive_loop())

    async def _send_waha(self, phone: str, text: str) -> dict[str, Any]:
        chat_id = phone if "@" in phone else f"{phone}@c.us"
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for chunk in chunks:
                for attempt in range(3):
                    try:
                        r = await client.post(
                            f"{WAHA_URL}/api/sendText",
                            json={"session": WAHA_SESSION, "chatId": chat_id, "text": chunk},
                            headers=_waha_headers(),
                        )
                        if r.status_code < 400:
                            break
                        logger.warning("WAHA send error %d (attempt %d): %s", r.status_code, attempt + 1, r.text[:200])
                    except Exception as exc:
                        logger.warning("WAHA send failed (attempt %d): %s", attempt + 1, exc)
                    if attempt < 2:
                        await asyncio.sleep(1 * (attempt + 1))
        return {"ok": True}

    async def _refresh_status(self, client: httpx.AsyncClient) -> None:
        try:
            r = await client.get(f"{WAHA_URL}/api/sessions/{WAHA_SESSION}", headers=_waha_headers())
            if r.status_code == 200:
                data = r.json()
                self._session_status = data.get("status", "unknown")
                me = data.get("me")
                if me:
                    self._my_number = str(me.get("id", "")).replace("@c.us", "")
        except Exception as exc:
            logger.debug("WAHA status check: %s", exc)

    async def _keepalive_loop(self) -> None:
        while _adapter_instance is self:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                    await self._refresh_status(client)
            except Exception:
                pass
            await asyncio.sleep(30)

    # -----------------------------------------------------------------------
    # Cloud API mode (legacy)
    # -----------------------------------------------------------------------

    def _start_cloud(self) -> None:
        if not WHATSAPP_APP_SECRET:
            logger.warning("WHATSAPP_APP_SECRET not set — webhook signature verification DISABLED")
        if not WHATSAPP_VERIFY_TOKEN:
            logger.warning("WHATSAPP_VERIFY_TOKEN not set — webhook challenge will fail")
        logger.info("WhatsApp adapter ready in Cloud API mode (webhook at /webhooks/whatsapp)")

    async def _send_cloud(self, channel_id: str, text: str) -> dict[str, Any]:
        api_version = os.getenv("WHATSAPP_API_VERSION", "v18.0")
        url = f"https://graph.facebook.com/{api_version}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for chunk in chunks:
                r = await client.post(url, json={
                    "messaging_product": "whatsapp", "to": channel_id,
                    "type": "text", "text": {"body": chunk}}, headers=headers)
                if r.status_code >= 400:
                    logger.warning("Cloud API send error %d: %s", r.status_code, r.text[:500])
        return {"ok": True}


# ---------------------------------------------------------------------------
# API: QR Code + Status
# ---------------------------------------------------------------------------

@router.get("/qr")
async def get_qr_code():
    """Get QR code for WhatsApp Web pairing (WAHA mode only)."""
    if WHATSAPP_MODE != "waha":
        return {"ok": False, "error": "QR code only available in WAHA mode"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.get(
                f"{WAHA_URL}/api/{WAHA_SESSION}/auth/qr?format=raw",
                headers=_waha_headers(),
            )
            if r.status_code == 200:
                return {"ok": True, "qr_value": r.json().get("value", ""), "status": "SCAN_QR_CODE"}
            return {"ok": False, "status": "not_ready", "detail": r.text[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/status")
async def whatsapp_status():
    """Get WhatsApp adapter status."""
    if _adapter_instance:
        return _adapter_instance.get_status()
    return {"mode": WHATSAPP_MODE, "session_status": "not_started"}


# ---------------------------------------------------------------------------
# Webhooks (WAHA + Cloud API)
# ---------------------------------------------------------------------------

@router.get("")
async def verify_webhook(request: Request):
    """Meta Cloud API webhook verification (challenge-response)."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp webhook verified")
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403, content="Verification failed")


@router.post("")
async def receive_webhook(request: Request):
    """Receive incoming messages (WAHA webhook or Meta Cloud API)."""
    body_bytes = await request.body()
    data = await request.json()

    if WHATSAPP_MODE == "waha":
        return await _handle_waha_webhook(data)
    return await _handle_cloud_webhook(body_bytes, data, request)


async def _handle_waha_webhook(data: dict) -> Response:
    try:
        event = data.get("event", "")
        if event not in ("message", "message.any"):
            return Response(status_code=200, content="OK")

        payload = data.get("payload", data.get("message", {}))
        from_number = str(payload.get("from", "")).replace("@c.us", "")
        body = payload.get("body", "") or payload.get("text", {}).get("body", "")
        is_from_me = payload.get("fromMe", False)

        # In self-chat mode, process messages from self
        # In DM mode, skip own messages to avoid loops
        if is_from_me and _adapter_instance and _adapter_instance._my_number:
            # Self-chat: message to yourself = command to the bot
            to = str(payload.get("to", "")).replace("@c.us", "")
            if to != _adapter_instance._my_number:
                return Response(status_code=200, content="OK")

        if not body or not from_number or not _adapter_instance:
            return Response(status_code=200, content="OK")

        response_text = await _adapter_instance.handle_incoming(
            platform_user_id=from_number,
            text=body,
            platform_name="whatsapp",
            extra={"from_number": from_number, "from_me": is_from_me},
        )

        reply_to = from_number if not is_from_me else _adapter_instance._my_number
        await _adapter_instance.send_message(reply_to, response_text)

    except Exception as exc:
        logger.warning("WAHA webhook error: %s", exc)

    return Response(status_code=200, content="OK")


async def _handle_cloud_webhook(body_bytes: bytes, data: dict, request: Request) -> Response:
    signature = request.headers.get("X-Hub-Signature-256", "")
    if WHATSAPP_APP_SECRET:
        expected = hmac.new(WHATSAPP_APP_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.replace("sha256=", "")):
            return Response(status_code=403, content="Invalid signature")

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        messages = changes.get("value", {}).get("messages", [])

        for msg in messages:
            if msg.get("type") != "text":
                continue
            from_number = msg.get("from", "")
            text = msg.get("text", {}).get("body", "")
            if not text or not from_number or not _adapter_instance:
                continue
            response_text = await _adapter_instance.handle_incoming(
                platform_user_id=from_number, text=text,
                platform_name="whatsapp", extra={"from_number": from_number})
            await _adapter_instance.send_message(from_number, response_text)
    except Exception as exc:
        logger.warning("Cloud webhook error: %s", exc)

    return Response(status_code=200, content="OK")
