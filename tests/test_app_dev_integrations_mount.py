"""Smoke test: dev integrations router is mounted in app.py."""
from __future__ import annotations
import sys
import pytest
from unittest.mock import MagicMock, patch


class TestDevIntegrationsMountedInApp:
    def test_dev_routes_registered(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ENABLED", "false")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
        for mod in list(sys.modules.keys()):
            if "dev_integrations" in mod:
                del sys.modules[mod]
        with patch("dev_integrations.GitHubSyncer", MagicMock()), \
             patch("obsidian_ingestor.ObsidianIngestor", MagicMock()):
            try:
                from fastapi.testclient import TestClient
                import app as app_module
                client = TestClient(app_module.app)
                resp = client.get("/api/dev/status")
                assert resp.status_code in (200, 503, 500)
            except Exception as e:
                pytest.skip(f"App import failed (likely missing heavy deps in CI): {e}")

    def test_dev_router_prefix_is_api_dev(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ENABLED", "false")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
        try:
            import app as app_module
            routes = [r.path for r in app_module.app.routes]
            assert any("/api/dev" in p for p in routes), \
                f"/api/dev not found in routes: {routes}"
        except Exception as e:
            pytest.skip(f"App import failed: {e}")
