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
                    r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=5)
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
    # Main execution loop (LLM + delivery)
    # ------------------------------------------------------------------

    async def run(self, job_id: str) -> None:
        """Full job execution: collect → LLM → deliver → persist."""
        db = sqlite3.connect(self._db_path)
        try:
            row = db.execute(
                "SELECT name, cron, prompt, sections, channels, timeout_s, last_run, last_status "
                "FROM scheduled_jobs WHERE id=?", (job_id,)
            ).fetchone()
        finally:
            db.close()

        if not row:
            logger.warning("Job %s not found", job_id)
            return

        name, cron, prompt, sections_json, channels_json, timeout_s, last_run, last_status = row

        if last_status == "running":
            logger.info("Job %s already running, skipping", job_id)
            return

        sections = json.loads(sections_json or "[]")
        channels = json.loads(channels_json or "[]")
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        self._update_job_status(job_id, "running", None, None)
        try:
            self._insert_run(run_id, job_id, started_at, "running", None, None, None)
        except Exception:
            logger.exception("Failed to insert job run record for job %s", job_id)
            self._update_job_status(job_id, "error", started_at, None)
            return

        output = None
        error = None
        channels_ok: dict[str, bool] = {}
        try:
            sections_text = await asyncio.wait_for(
                self.collect_sections(sections, cron, last_run, prompt, name),
                timeout=float(timeout_s)
            )

            output = await asyncio.wait_for(
                self._call_llm(sections_text, name),
                timeout=float(timeout_s)
            )

            # Best-effort PII filtering
            try:
                from pii_scanner import scan_text  # type: ignore[import]
                output = scan_text(output)
            except Exception:
                pass

            channels_ok = await self._notifier.broadcast(channels, output)

            # Best-effort Qdrant storage
            if self._qdrant and output:
                try:
                    from qdrant_client.models import PointStruct  # type: ignore[import]
                    self._qdrant.upsert(
                        collection_name="conversation_summaries",
                        points=[PointStruct(
                            id=str(uuid.uuid4()),
                            vector=[0.0],
                            payload={"content": output[:500], "source": "scheduler",
                                     "job_id": job_id, "created_at": started_at}
                        )]
                    )
                except Exception:
                    logger.exception("Failed to store briefing in Qdrant")

            status = "ok"
        except asyncio.TimeoutError:
            status = "timeout"
            error = f"Job exceeded timeout of {timeout_s}s"
            logger.warning("Job %s timed out after %ss", job_id, timeout_s)
        except Exception as e:
            status = "error"
            error = str(e)
            logger.exception("Job %s failed", job_id)

        finished_at = datetime.now(timezone.utc)
        duration_ms = int(
            (finished_at - datetime.fromisoformat(started_at)).total_seconds() * 1000
        )
        output_preview = (output or "")[:500]

        self._update_job_status(job_id, status, started_at, output_preview)
        self._finalize_run(run_id, status, duration_ms,
                           (output or "")[:2000], error, json.dumps(channels_ok))

    async def _call_llm(self, context: str, job_name: str) -> str:
        """Call LLM via AdaptiveRouter for briefing generation."""
        try:
            import json as _json
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "config", "model_router.json"
            )
            with open(config_path) as f:
                router_cfg = _json.load(f)

            task_routes = router_cfg.get("task_routes", {})
            candidates_keys = task_routes.get("briefing", task_routes.get("classify_query", []))
            profiles = router_cfg.get("profiles", {})

            candidate_models = []
            for key in candidates_keys:
                p = profiles.get(key, {})
                model = p.get("model")
                if model:
                    candidate_models.append(model)

            from adaptive_router import AdaptiveRouter  # type: ignore[import]
            ar = AdaptiveRouter()
            ranked = ar.get_model_ranking("briefing", candidate_models) or candidate_models

            messages = [
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant personnel. Génère un briefing clair et structuré "
                        "en Markdown à partir des données fournies. Sois concis."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Briefing pour '{job_name}':\n\n{context}",
                },
            ]

            import litellm  # type: ignore[import]
            for model in ranked:
                try:
                    resp = await litellm.acompletion(
                        model=model, messages=messages, max_tokens=800
                    )
                    result = resp.choices[0].message.content or ""
                    ar.record_quality("briefing", model, 0.8)
                    return result
                except Exception:
                    logger.warning("Model %s failed for briefing, trying next", model)

            return "Briefing indisponible — tous les modèles ont échoué."
        except Exception as e:
            logger.exception("LLM call failed for briefing")
            return f"Briefing indisponible: {e}"

    def _update_job_status(self, job_id: str, status: str, last_run: str | None,
                            last_output: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "UPDATE scheduled_jobs SET last_status=?, last_run=COALESCE(?,last_run), "
                "last_output=COALESCE(?,last_output), updated_at=? WHERE id=?",
                (status, last_run, last_output, now, job_id)
            )
            db.commit()
        finally:
            db.close()

    def _insert_run(self, run_id: str, job_id: str, started_at: str,
                     status: str, output: str | None, error: str | None,
                     channels_ok: str | None) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "INSERT INTO job_runs "
                "(id, job_id, started_at, status, output, error, channels_ok) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, job_id, started_at, status, output, error, channels_ok)
            )
            db.commit()
        finally:
            db.close()

    def _finalize_run(self, run_id: str, status: str, duration_ms: int,
                       output: str, error: str | None, channels_ok: str) -> None:
        db = sqlite3.connect(self._db_path)
        try:
            db.execute(
                "UPDATE job_runs "
                "SET status=?, duration_ms=?, output=?, error=?, channels_ok=? WHERE id=?",
                (status, duration_ms, output, error, channels_ok, run_id)
            )
            db.commit()
        finally:
            db.close()
