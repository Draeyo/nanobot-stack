"""Discord channel adapter using discord.py (soft import)."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from channels import ChannelAdapter

logger = logging.getLogger("rag-bridge.channels.discord")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_ALLOWED_CHANNEL_IDS = os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "")


class DiscordAdapter(ChannelAdapter):
    name = "discord"

    def __init__(self) -> None:
        super().__init__()
        self._token = DISCORD_BOT_TOKEN
        self._allowed_channels: set[str] = set()
        if DISCORD_ALLOWED_CHANNEL_IDS:
            self._allowed_channels = {c.strip() for c in DISCORD_ALLOWED_CHANNEL_IDS.split(",") if c.strip()}
        self._client = None

    def is_configured(self) -> bool:
        return bool(self._token)

    async def start(self) -> None:
        try:
            import discord
        except ImportError:
            logger.warning("discord.py not installed — Discord adapter disabled. "
                           "Install with: pip install discord.py")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        adapter = self  # capture for closure

        @self._client.event
        async def on_ready():
            logger.info("Discord adapter connected as %s", self._client.user)

        @self._client.event
        async def on_message(message):
            # Ignore own messages
            if message.author == self._client.user:
                return
            # Ignore bots
            if message.author.bot:
                return
            # Access control
            if adapter._allowed_channels and str(message.channel.id) not in adapter._allowed_channels:
                return

            if not message.content:
                return

            response_text = await adapter.handle_incoming(
                platform_user_id=str(message.author.id),
                text=message.content,
                platform_name="discord",
                extra={
                    "channel_id": str(message.channel.id),
                    "guild_id": str(message.guild.id) if message.guild else "",
                },
            )

            # Discord limit: 2000 chars per message
            chunks = [response_text[i:i + 1900] for i in range(0, len(response_text), 1900)]
            for chunk in chunks:
                await message.channel.send(chunk)

        logger.info("Discord adapter starting...")
        await self._client.start(self._token)

    async def stop(self) -> None:
        if self._client and not self._client.is_closed():
            await self._client.close()

    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        if not self._client or self._client.is_closed():
            return {"ok": False, "error": "Discord client not connected"}
        channel = self._client.get_channel(int(channel_id))
        if not channel:
            return {"ok": False, "error": f"channel {channel_id} not found"}
        chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
        for chunk in chunks:
            await channel.send(chunk)
        return {"ok": True}
