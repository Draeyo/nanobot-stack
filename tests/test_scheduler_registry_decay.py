"""Tests for Memory Decay Scan job registration in scheduler_registry."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


class TestMemoryDecayJobRegistration:

    def _make_registry(self, monkeypatch, enabled="true"):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", enabled)
        mgr = MagicMock()
        mgr.list_jobs.return_value = []
        import importlib
        import scheduler_registry
        importlib.reload(scheduler_registry)
        return scheduler_registry.JobRegistry(mgr), mgr

    def test_decay_job_registered_when_enabled(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="true")
        registry.seed()
        all_names = [c.kwargs.get("name", "") for c in mgr.create_job.call_args_list]
        assert "Memory Decay Scan" in all_names

    def test_decay_job_cron_is_weekly_monday(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="true")
        registry.seed()
        decay_calls = [
            c for c in mgr.create_job.call_args_list
            if c.kwargs.get("name") == "Memory Decay Scan"
        ]
        assert len(decay_calls) == 1
        assert decay_calls[0].kwargs["cron"] == "0 3 * * 1"

    def test_decay_job_not_registered_when_disabled(self, monkeypatch):
        registry, mgr = self._make_registry(monkeypatch, enabled="false")
        registry.seed()
        all_names = [c.kwargs.get("name", "") for c in mgr.create_job.call_args_list]
        assert "Memory Decay Scan" not in all_names

    def test_seed_is_idempotent_when_system_jobs_exist(self, monkeypatch):
        monkeypatch.setenv("MEMORY_DECAY_ENABLED", "true")
        mgr = MagicMock()
        mgr.list_jobs.return_value = [{"system": 1, "name": "Briefing matinal"}]
        import importlib
        import scheduler_registry
        importlib.reload(scheduler_registry)
        registry = scheduler_registry.JobRegistry(mgr)
        registry.seed()
        mgr.create_job.assert_not_called()
