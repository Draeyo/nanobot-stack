"""JobRegistry — seeds system jobs on first startup."""
from __future__ import annotations
import logging
import os
from typing import Any

logger = logging.getLogger("rag-bridge.scheduler_registry")


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no", ""):
        return default
    return default


SYSTEM_JOBS = [
    {
        "name": "Briefing matinal",
        "cron": "0 8 * * *",
        "sections": ["system_health", "personal_notes", "reminders"],
        "channels": ["ntfy", "telegram", "discord", "whatsapp"],
        "prompt": "Bonjour ! Voici ton briefing du {{day}} {{date}}.",
        "timeout_s": 120,
    },
    {
        "name": "Surveillance système",
        "cron": "*/30 * * * *",
        "sections": ["system_health"],
        "channels": ["ntfy"],
        "prompt": "",
        "timeout_s": 30,
    },
    {
        "name": "Bilan hebdomadaire",
        "cron": "0 9 * * 1",
        "sections": ["weekly_summary"],
        "channels": ["ntfy", "telegram", "discord", "whatsapp"],
        "prompt": "Voici le bilan de la semaine.",
        "timeout_s": 120,
    },
    {
        "name": "RSS Sync",
        "cron": "*/30 * * * *",
        "sections": ["rss_sync"],
        "channels": [],
        "prompt": "",
        "timeout_s": 120,
    },
]

# Daily backup job — only included when BACKUP_ENABLED=true
_BACKUP_JOB = {
    "name": "daily_backup",
    "cron": os.getenv("BACKUP_CRON", "0 3 * * *"),
    "sections": ["backup"],
    "channels": [],
    "prompt": "",
    "timeout_s": 3600,
}


class JobRegistry:
    def __init__(self, manager: Any) -> None:
        self._mgr = manager

    def seed(self) -> None:
        """Create system jobs if none exist yet. Idempotent: only seeds if no system jobs present."""
        if any(j.get("system") for j in self._mgr.list_jobs()):
            return

        jobs = list(SYSTEM_JOBS)

        # Register the backup job only when BACKUP_ENABLED=true
        if _env_bool("BACKUP_ENABLED", False):
            jobs.append(_BACKUP_JOB)
            logger.info("BACKUP_ENABLED=true — registering daily_backup job")

        for job in jobs:
            self._mgr.create_job(
                name=job["name"],
                cron=job["cron"],
                sections=job["sections"],
                channels=job["channels"],
                prompt=job["prompt"],
                timeout_s=job["timeout_s"],
                system=1,
            )
        logger.info("Seeded %d system jobs", len(jobs))
