"""Channel adapter system for bidirectional messaging platforms.

Each adapter (Telegram, Discord, WhatsApp) receives user messages from its
platform, routes them through the smart-chat pipeline, and sends the response
back.  Adapters run as asyncio background tasks within the bridge process.
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.channels")

CHANNELS_ENABLED = os.getenv("CHANNELS_ENABLED", "true").lower() == "true"


class ChannelAdapter(ABC):
    """Base class for all channel adapters."""

    name: str = "base"

    def __init__(self) -> None:
        self._chat_fn: Callable | None = None

    def set_chat_fn(self, fn: Callable) -> None:
        self._chat_fn = fn

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (long-polling, websocket, or webhook listening)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the adapter."""

    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        """Send a message to a specific channel/user."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if this adapter has the required credentials configured."""

    async def handle_incoming(self, platform_user_id: str, text: str,
                               platform_name: str,
                               extra: dict[str, Any] | None = None) -> str:
        """Process an incoming message through the smart-chat pipeline.

        If DM pairing is enabled (CHANNEL_DM_POLICY="pairing"), unknown users
        receive a pairing code and must be approved before interaction.
        """
        platform_id = f"{platform_name}:{platform_user_id}"

        # --- DM pairing gate ---
        try:
            from dm_pairing import is_user_approved, create_pairing_code
            if not is_user_approved(platform_id):
                code = create_pairing_code(
                    platform_id, platform_name,
                    display_name=str(extra.get("display_name", "")) if extra else "",
                )
                logger.info("Pairing code %s issued for %s", code, platform_id)
                return (
                    f"Access required. Your pairing code is: {code}\n"
                    f"Please ask the server administrator to approve this code."
                )
        except ImportError:
            pass  # dm_pairing module not available — allow through

        session_id = platform_id
        messages = [{"role": "user", "content": text}]

        if not self._chat_fn:
            return "Chat service not available."

        try:
            result = self._chat_fn(messages, session_id)
            if isinstance(result, dict):
                return result.get("text", str(result))
            return str(result)
        except Exception as exc:
            logger.error("Channel %s chat error for user %s: %s",
                         platform_name, platform_user_id, exc)
            return "An error occurred while processing your message."


class ChannelManager:
    """Manages all channel adapter lifecycles."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._chat_fn: Callable | None = None

    def init(self, chat_fn: Callable) -> None:
        """Inject the smart-chat pipeline function."""
        self._chat_fn = chat_fn

    def register(self, adapter: ChannelAdapter) -> None:
        """Register an adapter if it has valid configuration."""
        if adapter.is_configured():
            adapter.set_chat_fn(self._chat_fn)
            self._adapters[adapter.name] = adapter
            logger.info("Channel adapter registered: %s", adapter.name)
        else:
            logger.debug("Channel adapter %s not configured, skipping", adapter.name)

    async def start_all(self) -> list[str]:
        """Start all configured adapters as background tasks."""
        started = []
        for name, adapter in self._adapters.items():
            try:
                task = asyncio.create_task(adapter.start(), name=f"channel-{name}")
                self._tasks[name] = task
                started.append(name)
                logger.info("Channel adapter started: %s", name)
            except Exception as exc:
                logger.warning("Failed to start channel adapter %s: %s", name, exc)
        return started

    async def stop_all(self) -> None:
        """Stop all running adapters."""
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as exc:
                logger.warning("Error stopping channel %s: %s", name, exc)
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
        self._tasks.clear()

    def get_adapter(self, name: str) -> ChannelAdapter | None:
        """Return a registered adapter by name, or None."""
        return self._adapters.get(name)

    def status(self) -> dict[str, Any]:
        """Return status of all registered adapters."""
        result = {}
        for name in self._adapters:
            task = self._tasks.get(name)
            running = task is not None and not task.done()
            error = None
            if task and task.done() and task.exception():
                error = str(task.exception())
            result[name] = {"configured": True, "running": running, "error": error}
        return result


channel_manager = ChannelManager()
