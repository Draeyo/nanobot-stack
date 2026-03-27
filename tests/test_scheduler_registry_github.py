"""Tests for GitHub sync job registration in scheduler_registry."""
from __future__ import annotations
import os
import pytest
from unittest.mock import MagicMock, patch


class TestGitHubSyncJobRegistration:
    def _make_registry(self, monkeypatch, github_enabled="true", interval="30"):
        monkeypatch.setenv("GITHUB_ENABLED", github_enabled)
        monkeypatch.setenv("GITHUB_SYNC_INTERVAL", interval)
        import importlib
        import sys
        sys.modules.pop("scheduler_registry", None)
        import scheduler_registry
        importlib.reload(scheduler_registry)
        mock_mgr = MagicMock()
        mock_mgr.list_jobs.return_value = []
        return scheduler_registry.JobRegistry(mock_mgr)

    def test_github_sync_job_registered_when_enabled(self, monkeypatch):
        registry = self._make_registry(monkeypatch, github_enabled="true")
        registry.seed()
        created_names = [
            call.kwargs.get("name") or call.args[0]
            for call in registry._mgr.create_job.call_args_list
        ]
        assert any("github" in n.lower() for n in created_names), \
            f"No GitHub job found in: {created_names}"

    def test_github_sync_job_not_registered_when_disabled(self, monkeypatch):
        registry = self._make_registry(monkeypatch, github_enabled="false")
        registry.seed()
        created_names = [
            str(call) for call in registry._mgr.create_job.call_args_list
        ]
        assert not any("github" in s.lower() for s in created_names)

    def test_github_sync_job_uses_configured_interval(self, monkeypatch):
        registry = self._make_registry(monkeypatch, github_enabled="true", interval="60")
        registry.seed()
        for call in registry._mgr.create_job.call_args_list:
            kwargs = call.kwargs
            name = kwargs.get("name", "")
            if "github" in name.lower():
                cron = kwargs.get("cron", "")
                assert "60" in cron or "1" in cron, f"Unexpected cron: {cron}"
                break

    def test_github_sync_job_has_no_channels(self, monkeypatch):
        registry = self._make_registry(monkeypatch, github_enabled="true")
        registry.seed()
        for call in registry._mgr.create_job.call_args_list:
            kwargs = call.kwargs
            if "github" in kwargs.get("name", "").lower():
                assert kwargs.get("channels") == []
                break

    def test_github_sync_job_section_is_github_sync(self, monkeypatch):
        registry = self._make_registry(monkeypatch, github_enabled="true")
        registry.seed()
        for call in registry._mgr.create_job.call_args_list:
            kwargs = call.kwargs
            if "github" in kwargs.get("name", "").lower():
                assert "github_sync" in kwargs.get("sections", [])
                break
