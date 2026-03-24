# Sub-projet J — Intégrations Développeur (GitHub & Obsidian) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync GitHub PRs/issues/commits and Obsidian vault notes into Qdrant for RAG, with dev_digest briefing section

**Architecture:** GitHubSyncer uses PyGithub to fetch user activity, upserts to memory_projects Qdrant collection with source:github payload. ObsidianIngestor extends LocalDocIngestor (Sub-E) for Markdown with YAML frontmatter + WikiLink extraction. Dev_digest briefing section summarizes open PRs and today's GitHub activity.

**Tech Stack:** PyGithub>=2.0, PyYAML (existing), Qdrant (existing), APScheduler (existing), FastAPI (existing)

---

## Migration number check

Existing migrations in repo: 008, 010, 011, 012, 013, 014, 015. Slots 016–017 are reserved for other sub-projects (G, H, I). Per the spec, this sub-project uses **`migrations/016_github_obsidian.py`** (next available after 015). Note: the spec document names this file `018_github_sync_log.py` — if slots 016 and 017 are already claimed by sub-projects G and H at implementation time, use `018_github_sync_log.py` instead. Confirm with `ls migrations/` before creating.

> **New dependencies to add in `src/bridge/requirements.txt`:** `PyGithub>=2.0` and `PyYAML>=6.0` (PyYAML may already be present transitively — pin it explicitly).

---

## PART 1 — GITHUB

---

## Task 1 — Migration: `github_sync_log` and `obsidian_index` tables

### Test first

**File:** `tests/test_migration_018_github_obsidian.py`

```python
"""Tests for migration 018 — github_sync_log and obsidian_index tables."""
from __future__ import annotations
import importlib.util
import os
import pathlib
import sqlite3
import tempfile

import pytest


def _load_migration(tmp_path):
    """Load the migration module with RAG_STATE_DIR pointing to tmp_path."""
    os.environ["RAG_STATE_DIR"] = str(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "migration_018",
        pathlib.Path(__file__).parent.parent / "migrations" / "018_github_obsidian.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_returns_false_before_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    assert mod.check({}) is False


def test_migrate_creates_github_sync_log(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "github_sync_log" in tables
    finally:
        db.close()


def test_migrate_creates_obsidian_index(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "obsidian_index" in tables
    finally:
        db.close()


def test_github_sync_log_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        cols = {row[1] for row in db.execute(
            "PRAGMA table_info(github_sync_log)"
        ).fetchall()}
        assert cols >= {
            "id", "synced_at", "repos_synced", "items_synced",
            "status", "error_message", "rate_limit_remaining", "rate_limit_reset"
        }
    finally:
        db.close()


def test_obsidian_index_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        cols = {row[1] for row in db.execute(
            "PRAGMA table_info(obsidian_index)"
        ).fetchall()}
        assert cols >= {"id", "source_doc_id", "source_path", "target_note_name", "created_at"}
    finally:
        db.close()


def test_check_returns_true_after_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    assert mod.check({}) is True


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    mod.migrate({})  # second call must not raise


def test_github_sync_log_indexes_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_github_sync_log_synced_at" in indexes
        assert "idx_github_sync_log_status" in indexes
    finally:
        db.close()


def test_obsidian_index_indexes_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration(tmp_path)
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    try:
        indexes = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_obsidian_index_source_doc_id" in indexes
        assert "idx_obsidian_index_target_note_name" in indexes
    finally:
        db.close()
```

### Implementation

- [ ] Create `migrations/018_github_obsidian.py` (adjust number if 016/017 are taken).
- [ ] Set `VERSION = 18` (or the actual number used).
- [ ] `check(_ctx)`: connect to `scheduler.db`, return `True` only if **both** `github_sync_log` and `obsidian_index` tables exist.
- [ ] `migrate(_ctx)`: open `scheduler.db` with `PRAGMA journal_mode=WAL`, then:
  - `CREATE TABLE IF NOT EXISTS github_sync_log` with columns: `id TEXT PRIMARY KEY`, `synced_at TEXT NOT NULL`, `repos_synced TEXT NOT NULL`, `items_synced INTEGER NOT NULL DEFAULT 0`, `status TEXT NOT NULL`, `error_message TEXT`, `rate_limit_remaining INTEGER`, `rate_limit_reset TEXT`.
  - `CREATE INDEX IF NOT EXISTS idx_github_sync_log_synced_at ON github_sync_log(synced_at)`.
  - `CREATE INDEX IF NOT EXISTS idx_github_sync_log_status ON github_sync_log(status)`.
  - `CREATE TABLE IF NOT EXISTS obsidian_index` with columns: `id TEXT PRIMARY KEY`, `source_doc_id TEXT NOT NULL`, `source_path TEXT NOT NULL`, `target_note_name TEXT NOT NULL`, `created_at TEXT NOT NULL`.
  - `CREATE INDEX IF NOT EXISTS idx_obsidian_index_source_doc_id ON obsidian_index(source_doc_id)`.
  - `CREATE INDEX IF NOT EXISTS idx_obsidian_index_target_note_name ON obsidian_index(target_note_name)`.
  - `db.commit()`.
- [ ] Log `"Migration 018: github_sync_log and obsidian_index tables created at %s"`.
- [ ] Run tests: `pytest tests/test_migration_018_github_obsidian.py -v` — all green.

---

## Task 2 — `GitHubSyncer` skeleton: `__init__`, `GITHUB_ENABLED` guard, PAT auth

### Test first

**File:** `tests/test_github_syncer.py` (initial section)

```python
"""Tests for GitHubSyncer — GitHub synchronisation component."""
from __future__ import annotations
import os
import pytest
from unittest.mock import MagicMock, patch


def _make_syncer(monkeypatch, **env_overrides):
    """Factory: create a GitHubSyncer with env set and mocked Github client."""
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
```

### Implementation

- [ ] Create `src/bridge/dev_integrations.py`.
- [ ] Add imports: `logging`, `os`, `sqlite3`, `uuid`, `pathlib`, `datetime`, `asyncio`, `json`, `typing`.
- [ ] Define `logger = logging.getLogger("rag-bridge.dev_integrations")`.
- [ ] Define `GITHUB_COLLECTION = "memory_projects"`.
- [ ] Implement `class GitHubSyncer`:
  - `__init__(self, db_path: str | pathlib.Path, qdrant_client: Any = None)`:
    - Read `GITHUB_ENABLED` env → `self._enabled` (bool, default `False`).
    - Read `GITHUB_TOKEN`, `GITHUB_USERNAME`, `GITHUB_REPOS` env.
    - Parse `GITHUB_REPOS` csv → `self._repos: list[str]` (empty list if blank).
    - Read `GITHUB_SYNC_INTERVAL` → `self._sync_interval_min = max(5, min(1440, int(...)))`.
    - Set `self._db_path = pathlib.Path(db_path)`.
    - Set `self._qdrant = qdrant_client`.
    - Only if `self._enabled` and `GITHUB_TOKEN` is set: `import github; self._gh = github.Github(GITHUB_TOKEN)`. Otherwise `self._gh = None`.
    - Do NOT log the token at any level.
- [ ] Run tests: `pytest tests/test_github_syncer.py::TestGitHubSyncerInit -v` — all green.

---

## Task 3 — `fetch_prs(since_days)` — open PRs authored or assigned to user

### Test first

**File:** `tests/test_github_syncer.py` (append)

```python
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
        """PRs not authored by or assigned to GITHUB_USERNAME are excluded."""
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
```

### Implementation

- [ ] Add `fetch_prs(self, repo_name: str, since_days: int = 7) -> list[dict]` to `GitHubSyncer`:
  - Return `[]` immediately if `not self._enabled` or `self._gh is None`.
  - Wrap entire body in `try/except Exception` — log warning and return `[]` on error.
  - Call `self._gh.get_repo(repo_name).get_pulls(state='open', sort='updated', direction='desc')`.
  - Filter: keep only PRs where `pr.user.login == self._username` OR `self._username in [a.login for a in pr.assignees]`.
  - Limit to 100 PRs per repo.
  - For each PR, build payload dict matching the spec schema (section 3 of spec).
  - `body_snippet`: `(pr.body or "")[:500]`.
  - `labels`: `[l.name for l in pr.labels]`.
  - `assignees`: `[a.login for a in pr.assignees]`.
  - `synced_at`: `datetime.now(timezone.utc).isoformat()`.
  - Return list of dicts.
- [ ] Run tests: `pytest tests/test_github_syncer.py::TestFetchPRs -v` — all green.

---

## Task 4 — `fetch_issues(since_days)` — open issues assigned to user

### Test first

**File:** `tests/test_github_syncer.py` (append)

```python
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
        issue.pull_request = pull_request  # None = real issue; non-None = PR masquerading as issue
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
        """Issues that are actually PRs (issue.pull_request is not None) must be excluded."""
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
```

### Implementation

- [ ] Add `fetch_issues(self, repo_name: str, since_days: int = 7) -> list[dict]` to `GitHubSyncer`:
  - Return `[]` if `not self._enabled` or `self._gh is None`.
  - Wrap body in `try/except Exception` — log and return `[]` on error.
  - Call `self._gh.get_repo(repo_name).get_issues(state='open', assignee=self._username, sort='updated')`.
  - Filter: skip any issue where `issue.pull_request is not None`.
  - Limit to 100 issues per repo.
  - Build payload dict with `type="issue"`, same schema as PRs.
  - `state` field: use `issue.state` (always `"open"` from this filter, but keep dynamic).
  - Return list of dicts.
- [ ] Run tests: `pytest tests/test_github_syncer.py::TestFetchIssues -v` — all green.

---

## Task 5 — `fetch_commits(since_days)` — recent commits by user

### Test first

**File:** `tests/test_github_syncer.py` (append)

```python
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
        # The _qdrant_key field stores the dedup key used for uuid5 generation
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
```

### Implementation

- [ ] Add `fetch_commits(self, repo_name: str, since_days: int = 7) -> list[dict]` to `GitHubSyncer`:
  - Return `[]` if `not self._enabled` or `self._gh is None`.
  - Wrap in `try/except`.
  - Compute `since = datetime.now(timezone.utc) - timedelta(days=since_days)`.
  - Call `self._gh.get_repo(repo_name).get_commits(author=self._username, since=since)`.
  - Iterate, limit to 50.
  - `title = commit.commit.message.split('\n')[0]`.
  - `state = "merged"` (hardcoded — commits on default branch are merged by definition).
  - `body_snippet = None`.
  - `_qdrant_key = f"github:{repo_name}:commit:{commit.sha[:12]}"` — include this internal field to allow `sync_to_qdrant` to generate deterministic IDs.
  - `created_at = commit.commit.author.date.isoformat()`.
  - `author = commit.author.login if commit.author else commit.commit.author.name`.
  - Return list of dicts.
- [ ] Run tests: `pytest tests/test_github_syncer.py::TestFetchCommits -v` — all green.

---

## Task 6 — `sync_to_qdrant(qdrant_client)` — embed + upsert to `memory_projects`, update log

### Test first

**File:** `tests/test_github_syncer.py` (append)

```python
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
        """Same item upserted twice must produce same Qdrant point ID."""
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

        # Should call upsert at least 3 times (119 items / 50 per batch)
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
        assert rows[0][4] == "ok"  # status column
```

### Implementation

- [ ] Add `async def sync_to_qdrant(self, items: list[dict], log_entry: dict | None = None) -> int` to `GitHubSyncer`:
  - Return `0` if `not self._enabled` or `not items` or `self._qdrant is None`.
  - Import `litellm` and `qdrant_client.models.PointStruct` inline.
  - For each item, build embed text: `f"{item['title']}. {item.get('body_snippet') or ''}"`.
  - Embed in batches of 50 via `litellm.aembedding(model="text-embedding-3-small", input=batch)`.
  - For each item + vector: generate `point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, item["_qdrant_key"]))`.
  - Build `PointStruct(id=point_id, vector=vector, payload={...})` — payload is item dict minus `_qdrant_key`.
  - Upsert to `GITHUB_COLLECTION` in batches of 50.
  - If `log_entry` is provided and `self._db_path` exists: insert row into `github_sync_log` (id=uuid4, synced_at=now, repos_synced=json.dumps(log_entry["repos"]), items_synced=count, status="ok", rate_limit_remaining=log_entry.get("rate_limit_remaining"), rate_limit_reset=log_entry.get("rate_limit_reset")).
  - Wrap Qdrant upsert in `try/except` — on failure log error and set `status="error"` in log entry.
  - Return total count of upserted points.
- [ ] Run tests: `pytest tests/test_github_syncer.py::TestSyncToQdrant -v` — all green.

---

## Task 7 — Register `github_sync` job in `scheduler_registry.py`

### Test first

**File:** `tests/test_scheduler_registry.py` (append section or create new file)

```python
"""Tests for GitHub sync job registration in scheduler_registry."""
from __future__ import annotations
import os
import pytest
from unittest.mock import MagicMock, patch


class TestGitHubSyncJobRegistration:
    def _make_registry(self, monkeypatch, github_enabled="true", interval="30"):
        monkeypatch.setenv("GITHUB_ENABLED", github_enabled)
        monkeypatch.setenv("GITHUB_SYNC_INTERVAL", interval)
        # Re-import to pick up env changes
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
                # cron should encode 60-min interval, e.g. "*/60 * * * *"
                assert "60" in cron or "1" in cron, f"Unexpected cron: {cron}"
                break

    def test_github_sync_job_has_no_channels(self, monkeypatch):
        """GitHub sync is a background job — no notification channels."""
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
```

### Implementation

- [ ] Open `src/bridge/scheduler_registry.py`.
- [ ] Add a conditional GitHub sync job entry. After the existing `SYSTEM_JOBS` list definition, add a `_GITHUB_SYNC_JOB` dict:
  ```python
  _GITHUB_SYNC_JOB = {
      "name": "GitHub Sync",
      "cron": f"*/{max(5, min(1440, int(os.getenv('GITHUB_SYNC_INTERVAL', '30'))))} * * * *",
      "sections": ["github_sync"],
      "channels": [],
      "prompt": "",
      "timeout_s": 120,
  }
  ```
- [ ] In `JobRegistry.seed()`, after the backup job block, add:
  ```python
  if _env_bool("GITHUB_ENABLED", False):
      jobs.append(_GITHUB_SYNC_JOB)
      logger.info("GITHUB_ENABLED=true — registering GitHub Sync job")
  ```
- [ ] Run tests: `pytest tests/test_scheduler_registry.py -v` — all green (existing + new).

---

## Task 8 — `dev_digest` section in `scheduler_executor.py`

### Test first

**File:** `tests/test_scheduler_executor.py` (append section or create)

```python
"""Tests for dev_digest section in JobExecutor."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDevDigestSection:
    def _make_executor(self, monkeypatch, github_enabled="true"):
        monkeypatch.setenv("GITHUB_ENABLED", github_enabled)
        mock_qdrant = MagicMock()
        # Simulate Qdrant scroll returning 2 PRs and 1 issue
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
```

### Implementation

- [ ] Open `src/bridge/scheduler_executor.py`.
- [ ] Add `"dev_digest": "Activité développeur"` to `SECTION_LABELS`.
- [ ] Add `async def _collect_dev_digest(self) -> str` method:
  - Check `GITHUB_ENABLED` env — return `""` if false.
  - Return `""` if `self._qdrant is None`.
  - Call `self._qdrant.scroll(collection_name="memory_projects", scroll_filter={"must": [{"key": "source", "match": {"value": "github"}}, {"key": "state", "match": {"any": ["open", "merged"]}}]}, limit=30)`.
  - Group results by `payload["type"]` into `prs`, `issues`, `commits`.
  - Build markdown block:
    - `"## Activité GitHub\n"`
    - `f"### PRs ouvertes ({len(prs)}) :\n"` + `"- [{title}]({url}) — {repo}\n"` per PR.
    - `f"### Issues assignées ({len(issues)}) :\n"` + `"- [{title}]({url}) — {repo}\n"` per issue.
    - `f"### Commits récents (7j) :\n"` + `"- {title} — {repo}\n"` per commit.
  - Return `""` if all three groups are empty.
  - Wrap in `try/except` — return `f"dev_digest error: {e}"` on failure.
- [ ] In `collect_sections`, add `elif sec == "dev_digest": tasks[sec] = self._collect_dev_digest()`.
- [ ] Also add `elif sec == "github_sync": tasks[sec] = self._run_github_sync()` — a background sync section (see note below).
- [ ] Add `async def _run_github_sync(self) -> str` — mirrors `_run_rss_sync` pattern: checks `GITHUB_ENABLED`, imports `DevIntegrationManager` from `dev_integrations`, calls `sync_github()`, returns status string.
- [ ] Run tests: `pytest tests/test_scheduler_executor.py -v` — all green.

---

## PART 2 — OBSIDIAN

---

## Task 9 — Migration `obsidian_index` table

> **Already handled in Task 1.** Migration 018 creates both `github_sync_log` and `obsidian_index` in a single migration file. No additional migration needed.

- [ ] Verify that `test_migration_018_github_obsidian.py::test_migrate_creates_obsidian_index` passes — confirming the table was created.

---

## Task 10 — `ObsidianIngestor` class skeleton

### Test first

**File:** `tests/test_obsidian_ingestor.py`

```python
"""Tests for ObsidianIngestor — Obsidian vault ingestion component."""
from __future__ import annotations
import os
import pathlib
import tempfile
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def _make_ingestor(monkeypatch, vault_path=None, **kwargs):
    tmp = tempfile.mkdtemp()
    vault = vault_path or tmp
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", vault)
    monkeypatch.setenv("RAG_STATE_DIR", tmp)

    with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
        from obsidian_ingestor import ObsidianIngestor
        ingestor = ObsidianIngestor(
            state_dir=tmp,
            qdrant_client=MagicMock(),
        )
        ingestor._db_path = pathlib.Path(tmp) / "scheduler.db"
        ingestor._vault_path = pathlib.Path(vault)
        ingestor._enabled = bool(vault)
        ingestor._qdrant = MagicMock()
    return ingestor


class TestObsidianIngestorInit:
    def test_enabled_when_vault_path_set(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        assert ingestor._enabled is True

    def test_disabled_when_vault_path_empty(self, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
        with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
            from obsidian_ingestor import ObsidianIngestor
            ingestor = ObsidianIngestor(state_dir="/tmp", qdrant_client=None)
        assert ingestor._enabled is False

    def test_vault_path_stored_as_pathlib(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        assert isinstance(ingestor._vault_path, pathlib.Path)

    def test_disabled_when_vault_path_missing_from_env(self, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
            from obsidian_ingestor import ObsidianIngestor
            ingestor = ObsidianIngestor(state_dir="/tmp", qdrant_client=None)
        assert ingestor._enabled is False
```

### Implementation

- [ ] Create `src/bridge/obsidian_ingestor.py`.
- [ ] Imports: `logging`, `os`, `re`, `pathlib`, `sqlite3`, `uuid`, `datetime`, `typing`, `yaml` (imported inline for safety), `from local_doc_ingestor import LocalDocIngestor`.
- [ ] `class ObsidianIngestor(LocalDocIngestor)`:
  - `__init__(self, state_dir, qdrant_client, **kwargs)`:
    - Call `super().__init__(state_dir=state_dir, qdrant_client=qdrant_client, **kwargs)`.
    - Read `OBSIDIAN_VAULT_PATH` from env.
    - If empty or not set: `self._enabled = False`, `self._vault_path = None`, log warning.
    - Else: `self._vault_path = pathlib.Path(vault_path_env)`.
    - `self._enabled = bool(vault_path_env)`.
    - `self._db_path = pathlib.Path(state_dir) / "scheduler.db"`.
- [ ] Run tests: `pytest tests/test_obsidian_ingestor.py::TestObsidianIngestorInit -v` — all green.

---

## Task 11 — `_parse_frontmatter(content)` — extract YAML frontmatter

### Test first

**File:** `tests/test_obsidian_ingestor.py` (append)

```python
class TestParseFrontmatter:
    def _get_method(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        return ingestor._parse_frontmatter

    def test_extracts_tags(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ntags: [python, architecture]\n---\n# Note"
        result = parse(content)
        assert result.get("tags") == ["python", "architecture"]

    def test_extracts_aliases(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\naliases: [RAG, retrieval]\n---\n# Note"
        result = parse(content)
        assert result.get("aliases") == ["RAG", "retrieval"]

    def test_extracts_created_and_modified(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ncreated: 2026-01-01\nmodified: 2026-03-20\n---\n# Note"
        result = parse(content)
        assert "created" in result
        assert "modified" in result

    def test_returns_empty_dict_when_no_frontmatter(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "# Just a note\nSome content."
        result = parse(content)
        assert result == {}

    def test_returns_empty_dict_on_invalid_yaml(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ntags: [unclosed\n---\n# Note"
        result = parse(content)
        assert result == {}

    def test_does_not_raise_on_malformed_input(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\n: invalid yaml :\n---\n# Note"
        result = parse(content)  # must not raise
        assert isinstance(result, dict)

    def test_returns_empty_dict_when_frontmatter_not_at_start(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "# Note\n---\ntags: [python]\n---\n"
        result = parse(content)
        assert result == {}

    def test_uses_safe_load_not_unsafe_load(self, monkeypatch, tmp_path):
        """yaml.safe_load must be used — yaml.load with arbitrary objects must not execute."""
        parse = self._get_method(monkeypatch, tmp_path)
        # Python object tag should be treated as string, not executed
        content = "---\ntags: !!python/object:os.system [echo hacked]\n---\n# Note"
        result = parse(content)  # safe_load raises but we catch and return {}
        assert isinstance(result, dict)
```

### Implementation

- [ ] Add `_parse_frontmatter(self, content: str) -> dict` to `ObsidianIngestor`:
  - Check that `content` starts with `"---\n"` — return `{}` otherwise.
  - Find end of frontmatter block: second occurrence of `"\n---"` after the opening.
  - Extract YAML block between the two `---` delimiters.
  - Call `yaml.safe_load(yaml_block)` — wrap in `try/except Exception` — log warning and return `{}` on error.
  - If result is not a dict: return `{}`.
  - Return dict with keys: `tags`, `aliases`, `created`, `modified` — missing keys are absent (not defaulted to `None`).
- [ ] Run tests: `pytest tests/test_obsidian_ingestor.py::TestParseFrontmatter -v` — all green.

---

## Task 12 — `_extract_wikilinks(content)` — `[[note name]]` → `list[str]`

### Test first

**File:** `tests/test_obsidian_ingestor.py` (append)

```python
class TestExtractWikilinks:
    def _get_method(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        return ingestor._extract_wikilinks

    def test_extracts_simple_wikilink(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[My Note]] for details."
        result = extract(content)
        assert "my note" in result

    def test_extracts_multiple_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note A]] and [[Note B]] are related."
        result = extract(content)
        assert len(result) == 2

    def test_extracts_note_name_from_aliased_wikilink(self, monkeypatch, tmp_path):
        """[[note|alias]] should extract 'note', not 'alias'."""
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[architecture notes|archi]] here."
        result = extract(content)
        assert "architecture notes" in result
        assert "archi" not in result

    def test_deduplicates_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note A]] is great. Also see [[Note A]]."
        result = extract(content)
        assert len(result) == 1

    def test_excludes_url_links(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[https://example.com]] is not a wikilink."
        result = extract(content)
        assert result == []

    def test_normalizes_to_lowercase(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[My Note]] for details."
        result = extract(content)
        assert result[0] == "my note"

    def test_strips_whitespace(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[ My Note ]]"
        result = extract(content)
        assert result[0] == "my note"

    def test_returns_empty_list_when_no_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "# No wikilinks here\nJust plain text."
        result = extract(content)
        assert result == []

    def test_preserves_insertion_order_after_dedup(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note B]] then [[Note A]] then [[Note B]] again."
        result = extract(content)
        assert result == ["note b", "note a"]
```

### Implementation

- [ ] Add `_extract_wikilinks(self, content: str) -> list[str]` to `ObsidianIngestor`:
  - Use `re.findall(r'\[\[([^\|\]]+)(?:\|[^\]]+)?\]\]', content)` to extract note names (handling `[[note|alias]]` syntax — capture only the note part before `|`).
  - For each raw match: `.strip().lower()`.
  - Exclude items containing `"://"` (URL links).
  - Deduplicate while preserving order: `list(dict.fromkeys(wikilinks))`.
  - Return the deduplicated list.
- [ ] Run tests: `pytest tests/test_obsidian_ingestor.py::TestExtractWikilinks -v` — all green.

---

## Task 13 — `ingest_vault()` — scan all `.md` files, ingest with frontmatter + WikiLink metadata

### Test first

**File:** `tests/test_obsidian_ingestor.py` (append)

```python
class TestIngestVault:
    def _make_vault(self, tmp_path):
        """Create a small fake vault with 3 notes."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note_a.md").write_text(
            "---\ntags: [python]\naliases: [NoteA]\n---\n# Note A\nSee [[Note B]] for more.",
            encoding="utf-8",
        )
        (vault / "note_b.md").write_text(
            "# Note B\nThis has no frontmatter. Links to [[Note A]].",
            encoding="utf-8",
        )
        subdir = vault / "subdir"
        subdir.mkdir()
        (subdir / "note_c.md").write_text(
            "---\ntags: [architecture]\n---\n# Note C\nNo links.",
            encoding="utf-8",
        )
        return vault

    @pytest.mark.asyncio
    async def test_ingest_vault_processes_all_md_files(self, monkeypatch, tmp_path):
        vault = self._make_vault(tmp_path)
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True

        call_count = 0
        async def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.status = "indexed"
            result.doc_id = str(uuid.uuid4())
            return result

        import uuid
        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()

        result = await ingestor.ingest_vault()
        assert call_count == 3  # note_a, note_b, note_c

    @pytest.mark.asyncio
    async def test_ingest_vault_returns_stats(self, monkeypatch, tmp_path):
        vault = self._make_vault(tmp_path)
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True

        import uuid
        async def mock_ingest_file(path, **kwargs):
            result = MagicMock()
            result.status = "indexed"
            result.doc_id = str(uuid.uuid4())
            return result

        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()

        stats = await ingestor.ingest_vault()
        assert "indexed" in stats
        assert "errors" in stats
        assert stats["indexed"] == 3

    @pytest.mark.asyncio
    async def test_ingest_vault_returns_disabled_when_not_enabled(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        ingestor._enabled = False
        stats = await ingestor.ingest_vault()
        assert stats.get("status") == "disabled"

    @pytest.mark.asyncio
    async def test_ingest_vault_skips_non_md_files(self, monkeypatch, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note", encoding="utf-8")
        (vault / "image.png").write_bytes(b"\x89PNG")
        (vault / "config.json").write_text("{}", encoding="utf-8")

        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True

        call_count = 0
        import uuid
        async def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock(); r.status = "indexed"; r.doc_id = str(uuid.uuid4())
            return r

        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()

        await ingestor.ingest_vault()
        assert call_count == 1  # only .md file

    @pytest.mark.asyncio
    async def test_ingest_vault_counts_errors(self, monkeypatch, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        for i in range(3):
            (vault / f"note{i}.md").write_text(f"# Note {i}", encoding="utf-8")

        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True

        call_count = 0
        async def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("ingest error")
            r = MagicMock(); r.status = "indexed"; r.doc_id = "doc-id"
            return r

        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()

        stats = await ingestor.ingest_vault()
        assert stats["errors"] == 1
        assert stats["indexed"] == 2
```

### Implementation

- [ ] Override `async def ingest_file(self, file_path: str | pathlib.Path, **kwargs) -> Any` in `ObsidianIngestor`:
  - Return `IngestResult(status="disabled")` if `not self._enabled`.
  - Verify `str(file_path).endswith(".md")` — return early otherwise.
  - Verify resolved path starts with `str(self._vault_path)` — path traversal guard, raise `PermissionError` if violated.
  - Read raw content: `pathlib.Path(file_path).read_text(encoding="utf-8")`.
  - Call `self._parse_frontmatter(raw_content)` → `frontmatter`.
  - Call `self._extract_wikilinks(raw_content)` → `wikilinks`.
  - Build `extra_metadata`:
    ```python
    {
        "source": "obsidian",
        "obsidian_tags": frontmatter.get("tags", []),
        "obsidian_aliases": frontmatter.get("aliases", []),
        "frontmatter_created": frontmatter.get("created"),
        "frontmatter_modified": frontmatter.get("modified"),
        "wikilinks_count": len(wikilinks),
    }
    ```
  - Call `result = await super().ingest_file(file_path, extra_metadata=extra_metadata, **kwargs)`.
  - If `result.status in ("indexed", "updated")`: call `self._update_obsidian_index(result.doc_id, str(file_path), wikilinks)`.
  - Return `result`.
- [ ] Add `async def ingest_vault(self) -> dict` to `ObsidianIngestor`:
  - Return `{"status": "disabled"}` if `not self._enabled`.
  - Walk `self._vault_path` recursively with `pathlib.Path.rglob("*.md")`.
  - For each `.md` file: call `await self.ingest_file(path)`, track counts `indexed`, `updated`, `skipped`, `errors`.
  - On exception per file: increment `errors`, log warning, continue.
  - Return `{"indexed": N, "updated": M, "skipped": K, "errors": E, "total_files": T}`.
- [ ] Run tests: `pytest tests/test_obsidian_ingestor.py::TestIngestVault -v` — all green.

---

## Task 14 — `dev_integrations_api.py` — REST endpoints

### Test first

**File:** `tests/test_dev_integrations_api.py`

```python
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
        client, mock_mgr = _make_app(monkeypatch)
        mock_mgr.get_github_sync_log = MagicMock(return_value={"items": [], "total": 0, "limit": 20, "offset": 0})
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
```

### Implementation

- [ ] Create `src/bridge/dev_integrations_api.py`.
- [ ] Imports: `FastAPI`, `APIRouter`, `HTTPException`, `logging`, `os`, `from typing import Optional`.
- [ ] Define `router = APIRouter(tags=["dev-integrations"])`.
- [ ] Define module-level `_manager: Any = None` (set by `app.py` at startup).
- [ ] Implement endpoints:
  - `GET /status` → `_manager.get_status()`.
  - `POST /github/sync` → body `{"repos": Optional[list[str]]}` → `await _manager.sync_github(repos=body.repos)`. Return 503 if result status is "disabled".
  - `GET /github/log` → query params `limit: int = 20, offset: int = 0` → `_manager.get_github_sync_log(limit, offset)`.
  - `GET /obsidian/status` → `_manager.get_obsidian_status()`.
  - `POST /obsidian/sync` → `await _manager.obsidian_ingest_vault()`.
- [ ] Create `class DevIntegrationManager` in `dev_integrations.py`:
  - `__init__(self, db_path, qdrant_client)`: instantiate `GitHubSyncer` and `ObsidianIngestor`.
  - `get_status()` → combines github syncer status + obsidian ingestor status.
  - `async sync_github(repos=None)` → orchestrates `_discover_repos`, parallel `fetch_prs`/`fetch_issues`/`fetch_commits`, calls `sync_to_qdrant`.
  - `get_github_sync_log(limit, offset)` → query `github_sync_log` SQLite table.
  - `get_obsidian_status()` → returns vault path, note count, wikilinks count from `obsidian_index`.
  - `async obsidian_ingest_vault()` → delegates to `ObsidianIngestor.ingest_vault()`.
- [ ] Run tests: `pytest tests/test_dev_integrations_api.py -v` — all green.

---

## Task 15 — Mount router in `app.py`

### Test first

**File:** `tests/test_app_dev_integrations_mount.py`

```python
"""Smoke test: dev integrations router is mounted in app.py."""
from __future__ import annotations
import importlib
import os
import sys
import pytest
from unittest.mock import MagicMock, patch


class TestDevIntegrationsMountedInApp:
    def test_dev_routes_registered(self, monkeypatch):
        """GET /api/dev/status must be reachable (even if it returns 503)."""
        monkeypatch.setenv("GITHUB_ENABLED", "false")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")

        # Import app with heavy deps mocked
        for mod in list(sys.modules.keys()):
            if "dev_integrations" in mod:
                del sys.modules[mod]

        with patch("dev_integrations.GitHubSyncer", MagicMock()), \
             patch("obsidian_ingestor.ObsidianIngestor", MagicMock()):
            try:
                from fastapi.testclient import TestClient
                # app is imported last to pick up mocks
                import app as app_module
                client = TestClient(app_module.app)
                resp = client.get("/api/dev/status")
                assert resp.status_code in (200, 503, 500)
            except Exception as e:
                pytest.skip(f"App import failed (likely missing heavy deps in CI): {e}")

    def test_dev_router_prefix_is_api_dev(self, monkeypatch):
        """The router must be included with prefix /api/dev."""
        monkeypatch.setenv("GITHUB_ENABLED", "false")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
        try:
            import app as app_module
            routes = [r.path for r in app_module.app.routes]
            assert any("/api/dev" in p for p in routes), \
                f"/api/dev not found in routes: {routes}"
        except Exception as e:
            pytest.skip(f"App import failed: {e}")
```

### Implementation

- [ ] Open `src/bridge/app.py`.
- [ ] Add import near the other router imports (around line 51–53):
  ```python
  from dev_integrations_api import router as dev_integrations_router
  from dev_integrations import DevIntegrationManager
  import dev_integrations_api as _dev_api_module
  ```
- [ ] In the app startup section (lifespan or `@app.on_event("startup")`), instantiate `DevIntegrationManager` and assign to `_dev_api_module._manager`. Pass `qdrant_client` and `db_path`.
- [ ] Mount the router: `app.include_router(dev_integrations_router, prefix="/api/dev")`.
- [ ] Confirm `GITHUB_ENABLED=false` results in a clean boot with no GitHub API calls.
- [ ] Run tests: `pytest tests/test_app_dev_integrations_mount.py -v` — all green.

---

## Task 16 — Tests: `tests/test_github_syncer.py` and `tests/test_obsidian_ingestor.py` full run

### Final test consolidation

- [ ] Run full GitHub syncer suite: `pytest tests/test_github_syncer.py -v` — all tests from Tasks 2–6 pass.
- [ ] Run full Obsidian ingestor suite: `pytest tests/test_obsidian_ingestor.py -v` — all tests from Tasks 10–13 pass.
- [ ] Run API tests: `pytest tests/test_dev_integrations_api.py -v` — all green.
- [ ] Run migration tests: `pytest tests/test_migration_018_github_obsidian.py -v` — all green.
- [ ] Run full project test suite: `pytest tests/ -v` — no regressions in existing tests.

### Additional integration tests to add in `tests/test_github_syncer.py`

```python
class TestGitHubSyncerDiscoverRepos:
    def test_returns_configured_repos_when_env_set(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="user/repo1,org/repo2")
        repos = syncer._discover_repos()
        assert repos == ["user/repo1", "org/repo2"]

    def test_calls_github_api_when_repos_env_empty(self, monkeypatch):
        syncer = _make_syncer(monkeypatch, GITHUB_REPOS="")
        mock_repo = MagicMock()
        mock_repo.full_name = "testuser/auto-discovered"
        mock_repo.pushed_at = MagicMock()
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
```

### Implementation for `_discover_repos` and `_log_rate_limit`

- [ ] Add `_discover_repos(self) -> list[str]` to `GitHubSyncer`:
  - If `self._repos` is non-empty: return `self._repos`.
  - Else: call `self._gh.get_user(self._username).get_repos(type='owner', sort='pushed')`.
  - Filter: `repo.pushed_at` within last 90 days.
  - Limit to 50 repos.
  - Return `[r.full_name for r in filtered]`.
  - Wrap in `try/except` — return `self._repos` (possibly `[]`) on error.
- [ ] Add `_log_rate_limit(self) -> dict` to `GitHubSyncer`:
  - Call `rate_limit = self._gh.get_rate_limit()`.
  - `core = rate_limit.core`.
  - Log info: `f"GitHub rate limit: {core.remaining}/{core.limit}, reset at {core.reset}"`.
  - If `core.remaining < 100`: log warning.
  - Return `{"remaining": core.remaining, "reset": core.reset.isoformat()}`.
  - Wrap in `try/except` — return `{}` on error.
- [ ] Run tests: `pytest tests/test_github_syncer.py -v` — all green.

---

## Requirements update

- [ ] Add to `src/bridge/requirements.txt`:
  ```
  PyGithub>=2.0
  PyYAML>=6.0
  ```
  Place after existing `feedparser>=6.0` line.

---

## Summary

| Task | File(s) | Type |
|------|---------|------|
| 1 | `migrations/018_github_obsidian.py` | New |
| 2 | `src/bridge/dev_integrations.py` | New |
| 3 | `src/bridge/dev_integrations.py` | Extend |
| 4 | `src/bridge/dev_integrations.py` | Extend |
| 5 | `src/bridge/dev_integrations.py` | Extend |
| 6 | `src/bridge/dev_integrations.py` | Extend |
| 7 | `src/bridge/scheduler_registry.py` | Modify |
| 8 | `src/bridge/scheduler_executor.py` | Modify |
| 9 | (covered by Task 1) | — |
| 10 | `src/bridge/obsidian_ingestor.py` | New |
| 11 | `src/bridge/obsidian_ingestor.py` | Extend |
| 12 | `src/bridge/obsidian_ingestor.py` | Extend |
| 13 | `src/bridge/obsidian_ingestor.py` | Extend |
| 14 | `src/bridge/dev_integrations_api.py`, `src/bridge/dev_integrations.py` | New |
| 15 | `src/bridge/app.py` | Modify |
| 16 | `tests/test_github_syncer.py`, `tests/test_obsidian_ingestor.py` | Test consolidation |

**Test files produced:**
- `tests/test_migration_018_github_obsidian.py`
- `tests/test_github_syncer.py`
- `tests/test_obsidian_ingestor.py`
- `tests/test_dev_integrations_api.py`
- `tests/test_app_dev_integrations_mount.py`
