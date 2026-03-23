"""Tests for BroadcastNotifier."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


class TestBroadcastNotifierNtfy:
    async def test_ntfy_channel_calls_send_notification(self, mock_send_notification):
        from broadcast_notifier import BroadcastNotifier
        notifier = BroadcastNotifier(channel_manager=MagicMock())
        result = await notifier.broadcast(["ntfy"], "Hello world")
        mock_send_notification.assert_called_once_with("Hello world")
        assert result["ntfy"] is True

    async def test_ntfy_failure_recorded(self):
        from broadcast_notifier import BroadcastNotifier
        with patch("tools.send_notification", new_callable=AsyncMock) as m:
            m.return_value = {"ok": False, "error": "timeout"}
            notifier = BroadcastNotifier(channel_manager=MagicMock())
            result = await notifier.broadcast(["ntfy"], "Hello")
            assert result["ntfy"] is False


class TestBroadcastNotifierChannels:
    async def test_telegram_fans_out_to_approved_users(self, mock_dm_pairing, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        telegram_adapter = AsyncMock()
        telegram_adapter.send_message = AsyncMock(return_value={"ok": True})
        mock_channel_manager.get_adapter.return_value = telegram_adapter

        notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
        result = await notifier.broadcast(["telegram"], "Briefing!")

        # Only telegram user (platform_id "telegram:111") should receive message
        telegram_adapter.send_message.assert_called_once_with("111", "Briefing!")
        assert result["telegram"] is True

    async def test_one_channel_failure_does_not_block_others(self, mock_dm_pairing, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        failing_adapter = AsyncMock()
        failing_adapter.send_message = AsyncMock(side_effect=Exception("network error"))
        mock_channel_manager.get_adapter.return_value = failing_adapter

        with patch("tools.send_notification", new_callable=AsyncMock) as ntfy_mock:
            ntfy_mock.return_value = {"ok": True}
            notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
            result = await notifier.broadcast(["ntfy", "telegram"], "Test")

        assert result["ntfy"] is True
        assert result["telegram"] is False  # failed, but ntfy succeeded

    async def test_no_approved_users_returns_true(self, mock_channel_manager):
        from broadcast_notifier import BroadcastNotifier
        with patch("dm_pairing.list_approved_users", return_value=[]):
            notifier = BroadcastNotifier(channel_manager=mock_channel_manager)
            result = await notifier.broadcast(["telegram"], "Hello")
        assert result["telegram"] is True  # no users = nothing to fail
