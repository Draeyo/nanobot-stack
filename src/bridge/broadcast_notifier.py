"""BroadcastNotifier — fan-out messages to all configured delivery channels."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("rag-bridge.broadcast_notifier")

VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp", "webpush"})


class BroadcastNotifier:
    """Delivers a message to one or more channels in parallel.

    Args:
        channel_manager: The ChannelManager instance from channels/__init__.py.
    """

    def __init__(self, channel_manager: Any) -> None:
        self._channel_manager = channel_manager

    async def broadcast(self, channels: list[str], message: str) -> dict[str, bool]:
        """Send message to each channel. Returns {channel: success}."""
        tasks: dict[str, Any] = {}
        for ch in channels:
            if ch in VALID_CHANNELS:
                tasks[ch] = self._deliver(ch, message)
            else:
                logger.warning("Unknown channel ignored: %s", ch)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {ch: (not isinstance(r, Exception) and r) for ch, r in zip(tasks.keys(), results)}

    async def _deliver(self, channel: str, message: str) -> bool:
        if channel == "ntfy":
            return await self._deliver_ntfy(message)
        if channel == "webpush":
            return await self._deliver_webpush(message)
        return await self._deliver_adapter(channel, message)

    async def _deliver_ntfy(self, message: str) -> bool:
        try:
            from tools import send_notification
            result = await send_notification(message)
            return bool(result.get("ok"))
        except Exception:
            logger.exception("ntfy delivery failed")
            return False

    async def _deliver_webpush(self, message: str) -> bool:
        push_enabled = os.getenv("PUSH_ENABLED", "false").lower() == "true"
        if not push_enabled:
            logger.debug("webpush channel skipped: PUSH_ENABLED=false")
            return False
        try:
            from push_notifications import PushNotificationManager  # pylint: disable=import-outside-toplevel
            mgr = PushNotificationManager()
            result = mgr.send_to_all(title="Nanobot", body=message, url="/")
            return result.get("sent", 0) > 0 or result.get("failed", 0) == 0
        except Exception:
            logger.exception("webpush delivery failed")
            return False

    async def _deliver_adapter(self, platform: str, message: str) -> bool:
        try:
            from dm_pairing import list_approved_users
            users = [
                u for u in list_approved_users()
                if u["platform_id"].split(":")[0] == platform
            ]
            if not users:
                return True  # no users to notify = not a failure

            adapter = self._channel_manager.get_adapter(platform)
            if adapter is None:
                logger.warning("No adapter registered for channel: %s", platform)
                return False

            success = True
            for user in users:
                numeric_id = user["platform_id"].split(":", 1)[1]
                try:
                    await adapter.send_message(numeric_id, message)
                except Exception:
                    logger.exception("Failed to deliver to %s via %s", user["platform_id"], platform)
                    success = False
            return success
        except Exception:
            logger.exception("%s delivery failed", platform)
            return False
