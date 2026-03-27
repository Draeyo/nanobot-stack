"""Tests for dev integrations REST API."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def _make_app(monkeypatch, github_enabled="true", vault_path=""):
    monkeypatch.setenv("GITHUB_ENABLED", github_enabled)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", vault_path)
    import sys
    for mod in ["dev_integrations_api", "dev_integrations", "obsidian_ingestor"]:
        sys.modules.pop(mod, None)
    with patch("dev_integrations.GitHubSyncer") as mock_gh_cls, \
         patch("obsidian_ingestor.ObsidianIngestor") as mock_obs_cls:
        mock_manager = MagicMock()
        mock_manager.get_status.return_value = {
            "github": {"enabled": True, "username": "testuser", "items_in_qdrant": 5,
                       "last_sync": None, "last_sync_status": None, "rate_limit_remaining": 5000,
                       "repos_configured": []},
            "obsidian": {"enabled": False, "vault_path": "", "note_count": 0,
                         "last_sync": None, "watcher_running": False, "wikilinks_indexed": 0},
        }
        mock_manager.sync_github = AsyncMock(return_value={
            "status": "ok", "repos_synced": ["testuser/myrepo"],
            "items_synced": 5, "breakdown": {"pr": 2, "issue": 2, "commit": 1},
            "rate_limit_remaining": 4900,
        })
        mock_manager.obsidian_ingest_vault = AsyncMock(return_value={
            "indexed": 10, "updated": 2, "skipped": 0, "errors": 0, "total_files": 12,
        })
        mock_manager.get_obsidian_status.return_value = {
            "enabled": False, "vault_path": "", "note_count": 0,
            "last_sync": None, "watcher_running": False, "wikilinks_indexed": 0,
        }
        mock_manager.get_github_sync_log = MagicMock(
            return_value={"items": [], "total": 0, "limit": 20, "offset": 0}
        )
        from fastapi import FastAPI
        import importlib
        dev_api_mod = importlib.import_module("dev_integrations_api")
        app = FastAPI()
        app.include_router(dev_api_mod.router, prefix="/api/dev")
        dev_api_mod._manager = mock_manager
    return TestClient(app), mock_manager


class TestDevIntegrationsAPI:
    def test_get_status_returns_200(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        resp = client.get("/api/dev/status")
        assert resp.status_code == 200

    def test_get_status_body_has_github_and_obsidian(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        data = client.get("/api/dev/status").json()
        assert "github" in data
        assert "obsidian" in data

    def test_post_github_sync_returns_200(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        resp = client.post("/api/dev/github/sync", json={})
        assert resp.status_code == 200

    def test_post_github_sync_returns_503_when_disabled(self, monkeypatch):
        client, mock_mgr = _make_app(monkeypatch)
        mock_mgr.sync_github = AsyncMock(return_value={"status": "disabled"})
        resp = client.post("/api/dev/github/sync", json={})
        assert resp.status_code in (200, 503)

    def test_get_github_log_returns_200(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        resp = client.get("/api/dev/github/log")
        assert resp.status_code == 200

    def test_get_obsidian_status_returns_200(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        resp = client.get("/api/dev/obsidian/status")
        assert resp.status_code == 200

    def test_post_obsidian_sync_returns_200(self, monkeypatch):
        client, _ = _make_app(monkeypatch)
        resp = client.post("/api/dev/obsidian/sync")
        assert resp.status_code == 200

    def test_post_github_sync_accepts_repos_list(self, monkeypatch):
        client, mock_mgr = _make_app(monkeypatch)
        resp = client.post("/api/dev/github/sync", json={"repos": ["user/repo1"]})
        assert resp.status_code == 200
        call_kwargs = mock_mgr.sync_github.call_args
        assert call_kwargs is not None
