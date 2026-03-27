"""Tests for GitHubSyncer — GitHub synchronisation component."""
# pylint: disable=no-member  # MagicMock.return_value not recognized by pylint
from __future__ import annotations
import os
import pytest
from unittest.mock import MagicMock, patch


def _make_syncer(monkeypatch, **env_overrides):
    defaults = {
        "GITHUB_ENABLED": "true",
        "GITHUB_TOKEN": "ghp_testtoken123",
        "GITHUB_USERNAME": "testuser",
        "GITHUB_REPOS": "",
        "GITHUB_SYNC_INTERVAL": "30",
        "RAG_STATE_DIR": "/tmp/test_github_syncer",
    }
    defaults.update(env_overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)

    with patch("github.Github") as mock_gh_cls:
        mock_gh_cls.return_value = MagicMock()
        from dev_integrations import GitHubSyncer
        syncer = GitHubSyncer(db_path="/tmp/test_github_syncer/scheduler.db")
    return syncer


class TestGitHubSyncerInit:
    def test_enabled_when_env_true(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="true")
        assert syncer._enabled is True

    def test_disabled_when_env_false(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="false")
        assert syncer._enabled is False

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ENABLED", raising=False)
        with patch("github.Github"):
            from dev_integrations import GitHubSyncer
            syncer = GitHubSyncer(db_path="/tmp/test_github_syncer/scheduler.db")
        assert syncer._enabled is False

    def test_reads_username_from_env(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_USERNAME="myuser")
        assert syncer._username == "myuser"

    def test_reads_repos_csv(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="user/repo1,org/repo2")
        assert syncer._repos == ["user/repo1", "org/repo2"]

    def test_empty_repos_env_yields_empty_list(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="")
        assert syncer._repos == []

    def test_github_client_not_created_when_disabled(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ENABLED", "false")
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USERNAME", "u")
        with patch("github.Github") as mock_gh:
            from dev_integrations import GitHubSyncer
            GitHubSyncer(db_path="/tmp/test_github_syncer/scheduler.db")
        mock_gh.assert_not_called()

    def test_sync_interval_clamped_to_minimum_5(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_SYNC_INTERVAL="2")
        assert syncer._sync_interval_min >= 5

    def test_sync_interval_default_30(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_SYNC_INTERVAL="30")
        assert syncer._sync_interval_min == 30


class TestFetchPRs:
    def _make_mock_pr(self, number=1, title="feat: add X", body="Some description",
                       state="open", author="testuser", assignees=None, labels=None,
                       repo_name="testuser/myrepo"):
        pr = MagicMock()
        pr.number = number
        pr.title = title
        pr.body = body
        pr.state = state
        pr.html_url = f"https://github.com/{repo_name}/pull/{number}"
        pr.user.login = author
        pr.assignees = [MagicMock(login=a) for a in (assignees or ["testuser"])]
        pr.labels = [MagicMock(name=l) for l in (labels or ["enhancement"])]
        pr.created_at = MagicMock()
        pr.created_at.isoformat.return_value = "2026-03-20T10:00:00+00:00"
        pr.updated_at = MagicMock()
        pr.updated_at.isoformat.return_value = "2026-03-24T08:30:00+00:00"
        return pr

    def test_returns_list_of_dicts(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_pr = self._make_mock_pr()
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [mock_pr]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_pr_dict_has_required_fields(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_pr = self._make_mock_pr()
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [mock_pr]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        item = result[0]
        for field in ["source", "type", "repo", "title", "url", "state",
                       "labels", "body_snippet", "author", "assignees",
                       "created_at", "updated_at", "synced_at"]:
            assert field in item, f"Missing field: {field}"

    def test_pr_source_is_github(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_pr = self._make_mock_pr()
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [mock_pr]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert result[0]["source"] == "github"
        assert result[0]["type"] == "pr"

    def test_pr_body_truncated_to_500(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_pr = self._make_mock_pr(body="x" * 1000)
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [mock_pr]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert len(result[0]["body_snippet"]) <= 500

    def test_disabled_returns_empty_list(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="false")
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert result == []

    def test_filters_prs_by_username(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_USERNAME="testuser")
        other_pr = self._make_mock_pr(author="otheruser", assignees=["otheruser"])
        my_pr = self._make_mock_pr(author="testuser", assignees=[])
        mock_repo = MagicMock()
        mock_repo.get_pulls.return_value = [other_pr, my_pr]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert len(result) == 1
        assert result[0]["author"] == "testuser"

    def test_returns_empty_on_github_exception(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        syncer._gh.get_repo.side_effect = Exception("API error")
        result = syncer.fetch_prs("testuser/myrepo", since_days=7)
        assert result == []


class TestFetchIssues:
    def _make_mock_issue(self, number=5, title="bug: crash on startup", body="Steps to reproduce",
                          author="testuser", assignees=None, labels=None,
                          pull_request=None, repo_name="testuser/myrepo"):
        issue = MagicMock()
        issue.number = number
        issue.title = title
        issue.body = body
        issue.state = "open"
        issue.html_url = f"https://github.com/{repo_name}/issues/{number}"
        issue.user.login = author
        issue.assignees = [MagicMock(login=a) for a in (assignees or ["testuser"])]
        issue.labels = [MagicMock(name=l) for l in (labels or ["bug"])]
        issue.pull_request = pull_request
        issue.created_at = MagicMock()
        issue.created_at.isoformat.return_value = "2026-03-21T10:00:00+00:00"
        issue.updated_at = MagicMock()
        issue.updated_at.isoformat.return_value = "2026-03-24T09:00:00+00:00"
        return issue

    def test_returns_list_of_dicts(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_issue = self._make_mock_issue()
        mock_repo = MagicMock()
        mock_repo.get_issues.return_value = [mock_issue]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_issue_type_is_issue(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_issue = self._make_mock_issue()
        mock_repo = MagicMock()
        mock_repo.get_issues.return_value = [mock_issue]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        assert result[0]["type"] == "issue"
        assert result[0]["source"] == "github"

    def test_excludes_pull_requests(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        real_issue = self._make_mock_issue(number=5)
        pr_as_issue = self._make_mock_issue(number=6, pull_request=MagicMock())
        mock_repo = MagicMock()
        mock_repo.get_issues.return_value = [real_issue, pr_as_issue]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        assert len(result) == 1
        assert result[0]["url"].endswith("/5")

    def test_disabled_returns_empty_list(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="false")
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        assert result == []

    def test_returns_empty_on_exception(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        syncer._gh.get_repo.side_effect = Exception("network error")
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        assert result == []

    def test_issue_has_required_fields(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_issue = self._make_mock_issue()
        mock_repo = MagicMock()
        mock_repo.get_issues.return_value = [mock_issue]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_issues("testuser/myrepo", since_days=7)
        item = result[0]
        for field in ["source", "type", "repo", "title", "url", "state",
                       "labels", "body_snippet", "author", "assignees",
                       "created_at", "updated_at", "synced_at"]:
            assert field in item, f"Missing field: {field}"


class TestFetchCommits:
    def _make_mock_commit(self, sha="abc123def456", message="feat: initial commit",
                           author="testuser", repo_name="testuser/myrepo"):
        commit = MagicMock()
        commit.sha = sha
        commit.commit.message = message
        commit.html_url = f"https://github.com/{repo_name}/commit/{sha}"
        commit.commit.author.name = author
        commit.author = MagicMock()
        commit.author.login = author
        commit.commit.author.date = MagicMock()
        commit.commit.author.date.isoformat.return_value = "2026-03-24T07:00:00+00:00"
        return commit

    def test_returns_list_of_dicts(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commit = self._make_mock_commit()
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_commit_type_is_commit(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commit = self._make_mock_commit()
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result[0]["type"] == "commit"
        assert result[0]["source"] == "github"

    def test_commit_title_is_first_line_of_message(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commit = self._make_mock_commit(message="feat: add router\n\nDetailed body here.")
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result[0]["title"] == "feat: add router"

    def test_commit_state_is_merged(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commit = self._make_mock_commit()
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result[0]["state"] == "merged"

    def test_commit_body_snippet_is_none(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commit = self._make_mock_commit()
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result[0]["body_snippet"] is None

    def test_sha_truncated_to_12_chars_in_qdrant_id_key(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        sha = "abcdef123456789012"
        mock_commit = self._make_mock_commit(sha=sha)
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = [mock_commit]
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result[0]["_qdrant_key"].endswith(sha[:12])

    def test_limited_to_50_commits(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_commits = [self._make_mock_commit(sha=f"sha{i:012d}") for i in range(80)]
        mock_repo = MagicMock()
        mock_repo.get_commits.return_value = mock_commits
        syncer._gh.get_repo.return_value = mock_repo
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert len(result) <= 50

    def test_disabled_returns_empty_list(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="false")
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result == []

    def test_returns_empty_on_exception(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        syncer._gh.get_repo.side_effect = Exception("timeout")
        result = syncer.fetch_commits("testuser/myrepo", since_days=7)
        assert result == []


class TestSyncToQdrant:
    def _make_pr_item(self, number=1, repo="testuser/myrepo"):
        return {
            "source": "github",
            "type": "pr",
            "repo": repo,
            "title": "feat: add router",
            "url": f"https://github.com/{repo}/pull/{number}",
            "state": "open",
            "labels": ["enhancement"],
            "body_snippet": "Adds adaptive routing.",
            "author": "testuser",
            "assignees": ["testuser"],
            "created_at": "2026-03-20T10:00:00+00:00",
            "updated_at": "2026-03-24T08:30:00+00:00",
            "synced_at": "2026-03-24T09:00:00+00:00",
            "_qdrant_key": f"github:{repo}:pr:{number}",
        }

    @pytest.mark.asyncio
    async def test_returns_zero_when_disabled(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_ENABLED="false")
        result = await syncer.sync_to_qdrant([self._make_pr_item()])
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_qdrant(self, monkeypatch, tmp_path):
        syncer = _make_syncer(monkeypatch)
        syncer._qdrant = None
        result = await syncer.sync_to_qdrant([self._make_pr_item()])
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty_items(self, monkeypatch):
        syncer = _make_syncer(monkeypatch)
        mock_qdrant = MagicMock()
        syncer._qdrant = mock_qdrant
        result = await syncer.sync_to_qdrant([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_qdrant_upsert(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        syncer = _make_syncer(monkeypatch)
        mock_qdrant = MagicMock()
        syncer._qdrant = mock_qdrant
        items = [self._make_pr_item(i) for i in range(1, 4)]
        with patch("litellm.aembedding") as mock_embed:
            mock_embed.return_value = {
                "data": [{"embedding": [0.1] * 1536} for _ in items]
            }
            result = await syncer.sync_to_qdrant(items)
        mock_qdrant.upsert.assert_called()
        assert result == 3

    @pytest.mark.asyncio
    async def test_qdrant_id_is_deterministic(self, monkeypatch, tmp_path):
        import uuid
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        syncer = _make_syncer(monkeypatch)
        item = self._make_pr_item(1)
        expected_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, item["_qdrant_key"]))
        mock_qdrant = MagicMock()
        syncer._qdrant = mock_qdrant
        with patch("litellm.aembedding") as mock_embed:
            mock_embed.return_value = {"data": [{"embedding": [0.1] * 1536}]}
            await syncer.sync_to_qdrant([item])
        call_args = mock_qdrant.upsert.call_args
        points = call_args.kwargs.get("points") or call_args.args[1]
        assert str(points[0].id) == expected_id

    @pytest.mark.asyncio
    async def test_upserts_in_batches_of_50(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        syncer = _make_syncer(monkeypatch)
        mock_qdrant = MagicMock()
        syncer._qdrant = mock_qdrant
        items = [self._make_pr_item(i) for i in range(1, 120)]
        with patch("litellm.aembedding") as mock_embed:
            mock_embed.side_effect = lambda model, input: {
                "data": [{"embedding": [0.1] * 1536} for _ in input]
            }
            await syncer.sync_to_qdrant(items)
        assert mock_qdrant.upsert.call_count >= 3

    @pytest.mark.asyncio
    async def test_inserts_github_sync_log_row(self, monkeypatch, tmp_path):
        import sqlite3 as _sqlite3
        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        db_path = tmp_path / "scheduler.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE github_sync_log (
            id TEXT PRIMARY KEY, synced_at TEXT NOT NULL, repos_synced TEXT NOT NULL,
            items_synced INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            error_message TEXT, rate_limit_remaining INTEGER, rate_limit_reset TEXT
        )""")
        conn.commit()
        conn.close()
        syncer = _make_syncer(monkeypatch)
        syncer._db_path = db_path
        mock_qdrant = MagicMock()
        syncer._qdrant = mock_qdrant
        items = [self._make_pr_item(1)]
        with patch("litellm.aembedding") as mock_embed:
            mock_embed.return_value = {"data": [{"embedding": [0.1] * 1536}]}
            await syncer.sync_to_qdrant(items, log_entry={"repos": ["testuser/myrepo"],
                                                           "rate_limit_remaining": 4800,
                                                           "rate_limit_reset": "2026-03-24T10:00:00Z"})
        conn = _sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT * FROM github_sync_log").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][4] == "ok"


class TestGitHubSyncerDiscoverRepos:
    def test_returns_configured_repos_when_env_set(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="user/repo1,org/repo2")
        repos = syncer._discover_repos()
        assert repos == ["user/repo1", "org/repo2"]

    def test_calls_github_api_when_repos_env_empty(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="")
        mock_repo = MagicMock()
        mock_repo.full_name = "testuser/auto-discovered"
        from datetime import datetime, timezone, timedelta
        mock_repo.pushed_at = datetime.now(timezone.utc) - timedelta(days=10)
        syncer._gh.get_user.return_value.get_repos.return_value = [mock_repo]
        repos = syncer._discover_repos()
        assert "testuser/auto-discovered" in repos

    def test_auto_discovery_excludes_repos_older_than_90_days(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="")
        from datetime import datetime, timezone, timedelta
        old_repo = MagicMock()
        old_repo.full_name = "testuser/old-repo"
        old_repo.pushed_at = datetime.now(timezone.utc) - timedelta(days=100)
        recent_repo = MagicMock()
        recent_repo.full_name = "testuser/recent-repo"
        recent_repo.pushed_at = datetime.now(timezone.utc) - timedelta(days=5)
        syncer._gh.get_user.return_value.get_repos.return_value = [old_repo, recent_repo]
        repos = syncer._discover_repos()
        assert "testuser/old-repo" not in repos
        assert "testuser/recent-repo" in repos

    def test_auto_discovery_limited_to_50_repos(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="")
        from datetime import datetime, timezone, timedelta
        repos_list = []
        for i in range(80):
            r = MagicMock()
            r.full_name = f"testuser/repo{i}"
            r.pushed_at = datetime.now(timezone.utc) - timedelta(days=1)
            repos_list.append(r)
        syncer._gh.get_user.return_value.get_repos.return_value = repos_list
        repos = syncer._discover_repos()
        assert len(repos) <= 50


class TestGitHubSyncerRateLimit:
    def test_log_rate_limit_logs_warning_when_low(self, monkeypatch, caplog):
        import logging
        syncer = _make_syncer(monkeypatch)
        mock_rate = MagicMock()
        mock_rate.core.remaining = 50
        mock_rate.core.limit = 5000
        mock_rate.core.reset.isoformat.return_value = "2026-03-24T10:00:00+00:00"
        syncer._gh.get_rate_limit.return_value = mock_rate
        with caplog.at_level(logging.WARNING):
            syncer._log_rate_limit()
        assert any("rate limit" in r.message.lower() for r in caplog.records)

    def test_log_rate_limit_no_warning_when_sufficient(self, monkeypatch, caplog):
        import logging
        syncer = _make_syncer(monkeypatch)
        mock_rate = MagicMock()
        mock_rate.core.remaining = 4000
        mock_rate.core.limit = 5000
        mock_rate.core.reset.isoformat.return_value = "2026-03-24T10:00:00+00:00"
        syncer._gh.get_rate_limit.return_value = mock_rate
        with caplog.at_level(logging.WARNING):
            syncer._log_rate_limit()
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 0
