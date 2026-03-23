"""JobExecutor — collects sections, calls LLM, delivers via BroadcastNotifier."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("rag-bridge.scheduler_executor")

SECTION_LABELS = {
    "system_health": "Santé système",
    "personal_notes": "Notes récentes",
    "topics": "Sujets d'intérêt",
    "reminders": "Rappels",
    "weekly_summary": "Bilan de la semaine",
    "custom": "Note personnalisée",
}


class JobExecutor:
    """Executes a scheduled job: collect sections → LLM → deliver."""

    def __init__(self, db_path: str, notifier: Any, qdrant: Any = None) -> None:
        self._db_path = db_path
        self._notifier = notifier
        self._qdrant = qdrant

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------

    def _resolve_template(self, template: str, job_name: str = "",
                           last_run: str | None = None) -> str:
        now = datetime.now(timezone.utc)
        return (
            template
            .replace("{{date}}", now.strftime("%A %d %B %Y"))
            .replace("{{time}}", now.strftime("%H:%M"))
            .replace("{{day}}", now.strftime("%A"))
            .replace("{{hostname}}", socket.gethostname())
            .replace("{{job_name}}", job_name)
            .replace("{{last_run}}", last_run or "N/A")
        )

    def _notes_window_hours(self, cron: str, last_run: str | None) -> int:
        """Return the time window in hours for personal_notes queries."""
        interval_minutes = self._cron_interval_minutes(cron)
        if interval_minutes < 24 * 60:
            # sub-daily: use 1h if no last_run, else since last_run
            if last_run:
                try:
                    lr = datetime.fromisoformat(last_run)
                    delta = (datetime.now(timezone.utc) - lr).total_seconds() / 3600
                    return max(1, int(delta) + 1)
                except Exception:
                    pass
            return 1
        return 24

    def _is_high_frequency(self, cron: str) -> bool:
        """True if cron fires more often than every 6 hours."""
        return self._cron_interval_minutes(cron) < 6 * 60

    @staticmethod
    def _cron_interval_minutes(cron: str) -> int:
        """Estimate minimum interval in minutes for a cron expression."""
        try:
            from croniter import croniter
            c = croniter(cron)
            t1 = c.get_next(float)
            t2 = c.get_next(float)
            return max(1, int((t2 - t1) / 60))
        except Exception:
            return 1440  # assume daily on error

    # ------------------------------------------------------------------
    # Section collectors
    # ------------------------------------------------------------------

    async def _collect_system_health(self) -> str:
        try:
            import subprocess
            lines = []
            for cmd in [
                ["df", "-h", "/"],
                ["free", "-h"],
                ["uptime"],
            ]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    lines.append(r.stdout.strip())
                except Exception:
                    pass
            return "\n".join(lines) or "System info unavailable"
        except Exception as e:
            return f"system_health error: {e}"

    async def _collect_personal_notes(self, window_hours: int) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
            results = self._qdrant.scroll(
                collection_name="personal_memories",
                scroll_filter={"must": [{"key": "created_at", "range": {"gte": since}}]},
                limit=20,
            )
            points = results[0] if results else []
            if not points:
                return "Aucune nouvelle note."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"personal_notes error: {e}"

    async def _collect_reminders(self) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            results = self._qdrant.scroll(
                collection_name="personal_memories",
                scroll_filter={"must": [{"key": "tags", "match": {"any": ["reminder"]}}]},
                limit=20,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun rappel actif."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"reminders error: {e}"

    async def _collect_weekly_summary(self) -> str:
        if not self._qdrant:
            return "Qdrant not available"
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            results = self._qdrant.scroll(
                collection_name="conversation_summaries",
                scroll_filter={"must": [{"key": "created_at", "range": {"gte": since}}]},
                limit=30,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun résumé cette semaine."
            return "\n".join(f"- {p.payload.get('content', '')}" for p in points)
        except Exception as e:
            return f"weekly_summary error: {e}"

    async def _collect_topics(self) -> str:
        """Queries Qdrant documents. Expensive — avoid for high-freq jobs."""
        if not self._qdrant:
            return "Qdrant not available"
        try:
            results = self._qdrant.scroll(
                collection_name="documents",
                limit=10,
            )
            points = results[0] if results else []
            if not points:
                return "Aucun document indexé."
            snippets = "\n".join(f"- {p.payload.get('content', '')[:200]}" for p in points[:5])
            return snippets
        except Exception as e:
            return f"topics error: {e}"

    async def collect_sections(self, sections: list[str], cron: str,
                                last_run: str | None, prompt: str, job_name: str) -> str:
        """Collect all enabled sections in parallel and assemble the prompt."""
        tasks: dict[str, Any] = {}
        window_h = self._notes_window_hours(cron, last_run)

        for sec in sections:
            if sec == "system_health":
                tasks[sec] = self._collect_system_health()
            elif sec == "personal_notes":
                tasks[sec] = self._collect_personal_notes(window_h)
            elif sec == "reminders":
                tasks[sec] = self._collect_reminders()
            elif sec == "weekly_summary":
                tasks[sec] = self._collect_weekly_summary()
            elif sec == "topics":
                if self._is_high_frequency(cron):
                    logger.warning("Section 'topics' skipped for high-frequency job (cron=%s)", cron)
                else:
                    tasks[sec] = self._collect_topics()

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        section_data = dict(zip(tasks.keys(), results))

        parts = []
        for sec, data in section_data.items():
            label = SECTION_LABELS.get(sec, sec)
            content = data if isinstance(data, str) else f"Erreur: {data}"
            parts.append(f"## {label}\n{content}")

        if "custom" in sections and prompt:
            resolved = self._resolve_template(prompt, job_name=job_name, last_run=last_run)
            parts.append(f"## Note\n{resolved}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Main execution loop (LLM + delivery — implemented in Task 6)
    # ------------------------------------------------------------------

    async def run(self, job_id: str) -> None:
        """Full job execution. Implemented in Task 6."""
        raise NotImplementedError
