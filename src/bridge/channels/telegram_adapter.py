"""Telegram channel adapter using long-polling (httpx, no extra dependency)."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from channels import ChannelAdapter

logger = logging.getLogger("rag-bridge.channels.telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramAdapter(ChannelAdapter):
    name = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self._token = TELEGRAM_BOT_TOKEN
        self._allowed_chats: set[str] = set()
        if TELEGRAM_ALLOWED_CHAT_IDS:
            self._allowed_chats = {c.strip() for c in TELEGRAM_ALLOWED_CHAT_IDS.split(",") if c.strip()}
        self._running = False
        self._offset = 0

    def is_configured(self) -> bool:
        return bool(self._token)

    async def start(self) -> None:
        self._running = True
        logger.info("Telegram adapter starting long-polling...")
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while self._running:
                try:
                    updates = await self._get_updates(client)
                    for update in updates:
                        await self._process_update(update, client)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("Telegram polling error: %s", exc)
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Telegram limit: 4096 chars per message
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await client.post(
                    f"{TELEGRAM_API}{self._token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                )
        return {"ok": True}

    async def _get_updates(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get(
            f"{TELEGRAM_API}{self._token}/getUpdates",
            params={"offset": self._offset, "timeout": 30},
        )
        data = r.json()
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    async def _process_update(self, update: dict, client: httpx.AsyncClient) -> None:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        user_id = str(message.get("from", {}).get("id", ""))

        if not text or not chat_id:
            return

        # Access control
        if self._allowed_chats and chat_id not in self._allowed_chats:
            logger.debug("Telegram: ignoring message from non-allowed chat %s", chat_id)
            return

        response_text = await self.handle_incoming(
            platform_user_id=user_id,
            text=text,
            platform_name="telegram",
            extra={"chat_id": chat_id},
        )

        await self.send_message(chat_id, response_text)
