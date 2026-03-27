"""Tests for dev_digest section in JobExecutor."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDevDigestSection:
    def _make_executor(self, monkeypatch, github_enabled="true"):
        monkeypatch.setenv("GITHUB_ENABLED", github_enabled)
        mock_qdrant = MagicMock()
        pr1 = MagicMock()
        pr1.payload = {
            "source": "github", "type": "pr", "repo": "testuser/myrepo",
            "title": "feat: router", "url": "https://github.com/testuser/myrepo/pull/1",
            "state": "open", "labels": ["enhancement"], "updated_at": "2026-03-24T08:00:00Z",
        }
        issue1 = MagicMock()
        issue1.payload = {
            "source": "github", "type": "issue", "repo": "testuser/myrepo",
            "title": "bug: crash", "url": "https://github.com/testuser/myrepo/issues/5",
            "state": "open", "labels": ["bug"], "updated_at": "2026-03-24T07:00:00Z",
        }
        commit1 = MagicMock()
        commit1.payload = {
            "source": "github", "type": "commit", "repo": "testuser/myrepo",
            "title": "fix: null pointer", "url": "https://github.com/testuser/myrepo/commit/abc",
            "state": "merged", "created_at": "2026-03-24T06:00:00Z",
        }
        mock_qdrant.scroll.return_value = ([pr1, issue1, commit1], None)
        import sys
        sys.modules.pop("scheduler_executor", None)
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path="/tmp/test.db", notifier=MagicMock(), qdrant=mock_qdrant)
        return executor

    @pytest.mark.asyncio
    async def test_dev_digest_returns_string(self, monkeypatch):
        executor = self._make_executor(monkeypatch)
        result = await executor._collect_dev_digest()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_dev_digest_contains_github_header(self, monkeypatch):
        executor = self._make_executor(monkeypatch)
        result = await executor._collect_dev_digest()
        assert "GitHub" in result

    @pytest.mark.asyncio
    async def test_dev_digest_contains_prs_section(self, monkeypatch):
        executor = self._make_executor(monkeypatch)
        result = await executor._collect_dev_digest()
        assert "PR" in result or "Pull Request" in result or "feat: router" in result

    @pytest.mark.asyncio
    async def test_dev_digest_contains_issues_section(self, monkeypatch):
        executor = self._make_executor(monkeypatch)
        result = await executor._collect_dev_digest()
        assert "bug: crash" in result or "Issue" in result

    @pytest.mark.asyncio
    async def test_dev_digest_returns_empty_when_disabled(self, monkeypatch):
        executor = self._make_executor(monkeypatch, github_enabled="false")
        result = await executor._collect_dev_digest()
        assert result == ""

    @pytest.mark.asyncio
    async def test_dev_digest_returns_empty_when_no_qdrant(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ENABLED", "true")
        from scheduler_executor import JobExecutor
        executor = JobExecutor(db_path="/tmp/test.db", notifier=MagicMock(), qdrant=None)
        result = await executor._collect_dev_digest()
        assert result == ""

    @pytest.mark.asyncio
    async def test_dev_digest_is_routed_in_collect_sections(self, monkeypatch):
        executor = self._make_executor(monkeypatch)
        result = await executor.collect_sections(
            sections=["dev_digest"],
            cron="0 8 * * *",
            last_run=None,
            prompt="",
            job_name="Test",
        )
        assert "GitHub" in result or "dev_digest" in result.lower()
