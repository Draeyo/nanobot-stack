"""REST API for scheduler management — mounted at /api/scheduler."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator

logger = logging.getLogger("rag-bridge.scheduler_api")

router = APIRouter(prefix="/api/scheduler")

_manager: Any = None
_verify_token = None

VALID_SECTIONS = frozenset({"system_health", "personal_notes", "topics", "reminders", "weekly_summary", "custom"})
VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp"})


def init_scheduler_api(manager: Any, verify_token_dep: Any) -> None:
    global _manager, _verify_token
    _manager = manager
    _verify_token = verify_token_dep


def _auth():
    return [Depends(_verify_token)] if _verify_token else []


def _validate_cron(cron: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(cron):
        raise HTTPException(422, detail=f"Invalid cron expression: '{cron}'")


def _validate_sections(sections: list[str]) -> None:
    invalid = [s for s in sections if s not in VALID_SECTIONS]
    if invalid:
        raise HTTPException(422, detail=f"Invalid sections: {invalid}. Valid: {sorted(VALID_SECTIONS)}")


def _validate_channels(channels: list[str]) -> None:
    if not channels:
        raise HTTPException(422, detail="At least one channel is required")
    invalid = [c for c in channels if c not in VALID_CHANNELS]
    if invalid:
        raise HTTPException(422, detail=f"Invalid channels: {invalid}. Valid: {sorted(VALID_CHANNELS)}")


def _validate_topics_frequency(sections: list[str], cron: str) -> None:
    if "topics" not in sections:
        return
    try:
        from scheduler_executor import JobExecutor
        if JobExecutor._cron_interval_minutes(cron) < 6 * 60:
            raise HTTPException(400, detail="Section 'topics' cannot be used with cron intervals < 6h (LLM cost risk)")
    except HTTPException:
        raise
    except Exception:
        pass


class JobCreate(BaseModel):
    name: str
    cron: str
    sections: list[str]
    channels: list[str]
    prompt: str = ""
    timeout_s: int = 60

    @field_validator("timeout_s")
    @classmethod
    def timeout_range(cls, v: int) -> int:
        if not (10 <= v <= 300):
            raise ValueError("timeout_s must be between 10 and 300")
        return v


class JobUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    sections: list[str] | None = None
    channels: list[str] | None = None
    prompt: str | None = None
    timeout_s: int | None = None
    enabled: bool | None = None


@router.get("/jobs")
def list_jobs():
    return _manager.list_jobs()


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate):
    _validate_cron(body.cron)
    _validate_sections(body.sections)
    _validate_channels(body.channels)
    _validate_topics_frequency(body.sections, body.cron)
    job_id = _manager.create_job(
        name=body.name, cron=body.cron, sections=body.sections,
        channels=body.channels, prompt=body.prompt, timeout_s=body.timeout_s,
    )
    return _manager.get_job(job_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job


@router.put("/jobs/{job_id}")
def update_job(job_id: str, body: JobUpdate):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    updates = body.model_dump(exclude_none=True)
    if "cron" in updates:
        _validate_cron(updates["cron"])
    if "sections" in updates:
        _validate_sections(updates["sections"])
    if "channels" in updates:
        _validate_channels(updates["channels"])
    cron = updates.get("cron", job["cron"])
    sections = updates.get("sections", job["sections"])
    _validate_topics_frequency(sections, cron)
    _manager.update_job(job_id, **updates)
    return _manager.get_job(job_id)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        _manager.delete_job(job_id)
    except PermissionError as e:
        raise HTTPException(403, detail=str(e))


@router.post("/jobs/{job_id}/run")
def run_job_now(job_id: str, background_tasks: BackgroundTasks):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.get("last_status") == "running":
        raise HTTPException(409, detail="Job is already running")
    background_tasks.add_task(_manager._execute_job, job_id)
    return {"queued": True, "job_id": job_id}


@router.post("/jobs/{job_id}/toggle")
def toggle_job(job_id: str, enabled: bool):
    job = _manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    _manager.toggle_job(job_id, enabled)
    return _manager.get_job(job_id)


@router.get("/jobs/{job_id}/history")
def job_history(job_id: str, limit: int = 30, offset: int = 0):
    return _manager.get_job_history(job_id, limit=min(limit, 100), offset=offset)
