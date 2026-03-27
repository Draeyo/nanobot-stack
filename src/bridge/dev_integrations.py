"""dev_integrations — GitHub & Obsidian sync orchestration."""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.dev_integrations")

GITHUB_COLLECTION = "memory_projects"


class GitHubSyncer:
    """Syncs GitHub PRs, issues, and commits to Qdrant memory_projects collection."""

    def __init__(
        self,
        db_path: str | pathlib.Path,
        qdrant_client: Any = None,
    ) -> None:
        github_enabled = os.getenv("GITHUB_ENABLED", "false").lower() == "true"
        self._enabled: bool = github_enabled
        self._username: str = os.getenv("GITHUB_USERNAME", "")
        repos_env = os.getenv("GITHUB_REPOS", "")
        self._repos: list[str] = [r.strip() for r in repos_env.split(",") if r.strip()]
        interval_raw = int(os.getenv("GITHUB_SYNC_INTERVAL", "30"))
        self._sync_interval_min: int = max(5, min(1440, interval_raw))
        self._db_path: pathlib.Path = pathlib.Path(db_path)
        self._qdrant = qdrant_client
        self._gh: Any = None

        if self._enabled:
            token = os.getenv("GITHUB_TOKEN", "")
            if token:
                import github  # pylint: disable=import-outside-toplevel
                self._gh = github.Github(token)

    @property
    def enabled(self) -> bool:
        """Whether GitHub integration is enabled."""
        return self._enabled

    @property
    def username(self) -> str:
        """Configured GitHub username."""
        return self._username

    @property
    def repos(self) -> list[str]:
        """Configured repo list."""
        return self._repos

    def discover_repos(self) -> list[str]:
        """Public wrapper around _discover_repos."""
        return self._discover_repos()

    def log_rate_limit(self) -> dict:
        """Public wrapper around _log_rate_limit."""
        return self._log_rate_limit()

    def fetch_prs(self, repo_name: str, since_days: int = 7) -> list[dict]:  # pylint: disable=unused-argument
        """Fetch open PRs authored by or assigned to the configured user."""
        if not self._enabled or self._gh is None:
            return []
        try:
            repo = self._gh.get_repo(repo_name)
            pulls = repo.get_pulls(state="open", sort="updated", direction="desc")
            now = datetime.now(timezone.utc).isoformat()
            results = []
            for pr in pulls:
                if len(results) >= 100:
                    break
                is_author = pr.user.login == self._username
                is_assignee = self._username in [a.login for a in pr.assignees]
                if not (is_author or is_assignee):
                    continue
                results.append({
                    "source": "github",
                    "type": "pr",
                    "repo": repo_name,
                    "title": pr.title,
                    "url": pr.html_url,
                    "state": pr.state,
                    "labels": [lb.name for lb in pr.labels],
                    "body_snippet": (pr.body or "")[:500],
                    "author": pr.user.login,
                    "assignees": [a.login for a in pr.assignees],
                    "created_at": pr.created_at.isoformat(),
                    "updated_at": pr.updated_at.isoformat(),
                    "synced_at": now,
                    "_qdrant_key": f"github:{repo_name}:pr:{pr.number}",
                })
            return results
        except Exception:  # pylint: disable=broad-except
            logger.warning("fetch_prs failed for %s", repo_name, exc_info=True)
            return []

    def fetch_issues(self, repo_name: str, since_days: int = 7) -> list[dict]:  # pylint: disable=unused-argument
        """Fetch open issues assigned to the configured user."""
        if not self._enabled or self._gh is None:
            return []
        try:
            repo = self._gh.get_repo(repo_name)
            issues = repo.get_issues(state="open", assignee=self._username, sort="updated")
            now = datetime.now(timezone.utc).isoformat()
            results = []
            for issue in issues:
                if len(results) >= 100:
                    break
                if issue.pull_request is not None:
                    continue
                results.append({
                    "source": "github",
                    "type": "issue",
                    "repo": repo_name,
                    "title": issue.title,
                    "url": issue.html_url,
                    "state": issue.state,
                    "labels": [lb.name for lb in issue.labels],
                    "body_snippet": (issue.body or "")[:500],
                    "author": issue.user.login,
                    "assignees": [a.login for a in issue.assignees],
                    "created_at": issue.created_at.isoformat(),
                    "updated_at": issue.updated_at.isoformat(),
                    "synced_at": now,
                    "_qdrant_key": f"github:{repo_name}:issue:{issue.number}",
                })
            return results
        except Exception:  # pylint: disable=broad-except
            logger.warning("fetch_issues failed for %s", repo_name, exc_info=True)
            return []

    def fetch_commits(self, repo_name: str, since_days: int = 7) -> list[dict]:
        """Fetch recent commits authored by the configured user."""
        if not self._enabled or self._gh is None:
            return []
        try:
            repo = self._gh.get_repo(repo_name)
            since = datetime.now(timezone.utc) - timedelta(days=since_days)
            commits = repo.get_commits(author=self._username, since=since)
            now = datetime.now(timezone.utc).isoformat()
            results = []
            for commit in commits:
                if len(results) >= 50:
                    break
                message = commit.commit.message
                title = message.split("\n")[0]
                author = (
                    commit.author.login if commit.author else commit.commit.author.name
                )
                results.append({
                    "source": "github",
                    "type": "commit",
                    "repo": repo_name,
                    "title": title,
                    "url": commit.html_url,
                    "state": "merged",
                    "labels": [],
                    "body_snippet": None,
                    "author": author,
                    "assignees": [],
                    "created_at": commit.commit.author.date.isoformat(),
                    "updated_at": commit.commit.author.date.isoformat(),
                    "synced_at": now,
                    "_qdrant_key": f"github:{repo_name}:commit:{commit.sha[:12]}",
                })
            return results
        except Exception:  # pylint: disable=broad-except
            logger.warning("fetch_commits failed for %s", repo_name, exc_info=True)
            return []

    async def sync_to_qdrant(
        self,
        items: list[dict],
        log_entry: dict | None = None,
    ) -> int:
        """Embed items and upsert to memory_projects. Returns count upserted."""
        if not self._enabled or not items or self._qdrant is None:
            return 0

        import litellm  # pylint: disable=import-outside-toplevel
        from qdrant_client.models import PointStruct  # pylint: disable=import-outside-toplevel

        batch_size = 50
        total = 0
        status = "ok"
        try:
            for batch_start in range(0, len(items), batch_size):
                batch = items[batch_start: batch_start + batch_size]
                texts = [
                    f"{item['title']}. {item.get('body_snippet') or ''}".strip()
                    for item in batch
                ]
                response = await litellm.aembedding(
                    model="text-embedding-3-small",
                    input=texts,
                )
                vectors = [d["embedding"] for d in response["data"]]
                points = []
                for item, vector in zip(batch, vectors):
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, item["_qdrant_key"]))
                    payload = {k: v for k, v in item.items() if k != "_qdrant_key"}
                    points.append(PointStruct(id=point_id, vector=vector, payload=payload))
                self._qdrant.upsert(
                    collection_name=GITHUB_COLLECTION,
                    points=points,
                )
                total += len(points)
        except Exception:  # pylint: disable=broad-except
            logger.error("sync_to_qdrant upsert failed", exc_info=True)
            status = "error"

        if log_entry is not None and self._db_path.exists():
            self._write_sync_log(log_entry, total, status)

        return total

    def _write_sync_log(self, log_entry: dict, items_synced: int, status: str) -> None:
        """Insert a row into github_sync_log."""
        try:
            db = sqlite3.connect(str(self._db_path))
            try:
                db.execute(
                    "INSERT INTO github_sync_log "
                    "(id, synced_at, repos_synced, items_synced, status, "
                    "rate_limit_remaining, rate_limit_reset) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps(log_entry.get("repos", [])),
                        items_synced,
                        status,
                        log_entry.get("rate_limit_remaining"),
                        log_entry.get("rate_limit_reset"),
                    ),
                )
                db.commit()
            finally:
                db.close()
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to write github_sync_log", exc_info=True)

    def _discover_repos(self) -> list[str]:
        """Return configured repos or auto-discover from GitHub API (last 90d, max 50)."""
        if self._repos:
            return self._repos
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=90)
            all_repos = self._gh.get_user(self._username).get_repos(
                type="owner", sort="pushed"
            )
            filtered = [
                r.full_name
                for r in all_repos
                if r.pushed_at and r.pushed_at > cutoff
            ]
            return filtered[:50]
        except Exception:  # pylint: disable=broad-except
            logger.warning("_discover_repos failed", exc_info=True)
            return self._repos

    def _log_rate_limit(self) -> dict:
        """Log GitHub rate limit status. Returns remaining/reset dict."""
        try:
            rate_limit = self._gh.get_rate_limit()
            core = rate_limit.core  # pylint: disable=no-member
            logger.info(
                "GitHub rate limit: %d/%d, reset at %s",
                core.remaining,
                core.limit,
                core.reset,
            )
            if core.remaining < 100:
                logger.warning(
                    "GitHub rate limit low: %d remaining, reset at %s",
                    core.remaining,
                    core.reset,
                )
            return {"remaining": core.remaining, "reset": core.reset.isoformat()}
        except Exception:  # pylint: disable=broad-except
            logger.warning("_log_rate_limit failed", exc_info=True)
            return {}


class DevIntegrationManager:
    """Orchestrates GitHub sync and Obsidian ingestion."""

    def __init__(self, db_path: str | pathlib.Path, qdrant_client: Any = None) -> None:
        self._db_path = pathlib.Path(db_path)
        self._qdrant = qdrant_client
        self._syncer = GitHubSyncer(db_path=db_path, qdrant_client=qdrant_client)
        self._obsidian: Any = None
        self._init_obsidian()

    def _init_obsidian(self) -> None:
        try:
            from obsidian_ingestor import ObsidianIngestor  # pylint: disable=import-outside-toplevel
            self._obsidian = ObsidianIngestor(
                state_dir=str(self._db_path.parent),
                qdrant_client=self._qdrant,
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning("ObsidianIngestor init failed", exc_info=True)

    def get_status(self) -> dict:
        """Return combined github + obsidian status."""
        github_status = {
            "enabled": self._syncer.enabled,
            "username": self._syncer.username,
            "repos_configured": self._syncer.repos,
            "items_in_qdrant": 0,
            "last_sync": None,
            "last_sync_status": None,
            "rate_limit_remaining": None,
        }
        obsidian_status = self.get_obsidian_status()
        return {"github": github_status, "obsidian": obsidian_status}

    def get_obsidian_status(self) -> dict:
        """Return obsidian vault status."""
        if self._obsidian is None:
            return {"enabled": False, "vault_path": "", "note_count": 0,
                    "last_sync": None, "watcher_running": False, "wikilinks_indexed": 0}
        vault_path = self._obsidian.vault_path_str
        return {
            "enabled": self._obsidian.is_enabled,
            "vault_path": vault_path,
            "note_count": self._count_obsidian_notes(),
            "last_sync": None,
            "watcher_running": False,
            "wikilinks_indexed": self._count_wikilinks(),
        }

    def _count_obsidian_notes(self) -> int:
        if not self._obsidian or not self._obsidian.is_enabled or not self._obsidian.vault_path:
            return 0
        try:
            return sum(1 for _ in self._obsidian.vault_path.rglob("*.md"))
        except Exception:  # pylint: disable=broad-except
            return 0

    def _count_wikilinks(self) -> int:
        if not self._db_path.exists():
            return 0
        try:
            db = sqlite3.connect(str(self._db_path))
            try:
                count = db.execute("SELECT COUNT(*) FROM obsidian_index").fetchone()
                return count[0] if count else 0
            finally:
                db.close()
        except Exception:  # pylint: disable=broad-except
            return 0

    async def sync_github(self, repos: list[str] | None = None) -> dict:
        """Full GitHub sync across all configured repos."""
        if not self._syncer.enabled:
            return {"status": "disabled"}
        target_repos = repos or self._syncer.discover_repos()
        rate_info = self._syncer.log_rate_limit()
        all_items: list[dict] = []
        breakdown: dict[str, int] = {"pr": 0, "issue": 0, "commit": 0}
        for repo in target_repos:
            prs = self._syncer.fetch_prs(repo)
            issues = self._syncer.fetch_issues(repo)
            commits = self._syncer.fetch_commits(repo)
            all_items.extend(prs)
            all_items.extend(issues)
            all_items.extend(commits)
            breakdown["pr"] += len(prs)
            breakdown["issue"] += len(issues)
            breakdown["commit"] += len(commits)
        synced = await self._syncer.sync_to_qdrant(
            all_items,
            log_entry={
                "repos": target_repos,
                "rate_limit_remaining": rate_info.get("remaining"),
                "rate_limit_reset": rate_info.get("reset"),
            },
        )
        return {
            "status": "ok",
            "repos_synced": target_repos,
            "items_synced": synced,
            "breakdown": breakdown,
            "rate_limit_remaining": rate_info.get("remaining"),
        }

    def get_github_sync_log(self, limit: int = 20, offset: int = 0) -> dict:
        """Return paginated github_sync_log rows."""
        if not self._db_path.exists():
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        try:
            db = sqlite3.connect(str(self._db_path))
            db.row_factory = sqlite3.Row
            try:
                rows = db.execute(
                    "SELECT * FROM github_sync_log ORDER BY synced_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = db.execute("SELECT COUNT(*) FROM github_sync_log").fetchone()[0]
                return {
                    "items": [dict(r) for r in rows],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
            finally:
                db.close()
        except Exception:  # pylint: disable=broad-except
            logger.warning("get_github_sync_log failed", exc_info=True)
            return {"items": [], "total": 0, "limit": limit, "offset": offset}

    async def obsidian_ingest_vault(self) -> dict:
        """Trigger Obsidian vault ingestion."""
        if self._obsidian is None:
            return {"status": "disabled", "indexed": 0, "errors": 0}
        return await self._obsidian.ingest_vault()
