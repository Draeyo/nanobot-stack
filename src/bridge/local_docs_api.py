"""Local Docs API — FastAPI router for /api/docs/*."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.local_docs_api")

router = APIRouter(prefix="/api/docs", tags=["local-docs"])

# Injected at startup by app.py
_ingestor: Any = None  # pylint: disable=invalid-name


def init_local_docs_api(ingestor: Any) -> None:
    """Inject the LocalDocIngestor instance at startup."""
    global _ingestor  # pylint: disable=global-statement
    _ingestor = ingestor


def _get_ingestor() -> Any:
    """Return the active ingestor or raise 503."""
    if _ingestor is None:
        raise HTTPException(status_code=503, detail="LocalDocIngestor not initialised")
    return _ingestor


def _require_enabled() -> None:
    """Raise HTTP 503 if LOCAL_DOCS_ENABLED is false."""
    if os.getenv("LOCAL_DOCS_ENABLED", "false").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=503,
            detail="Local document ingestion is disabled. Set LOCAL_DOCS_ENABLED=true to enable.",
        )


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Request body for the ingest endpoint."""

    file_path: str


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/ingest")
def ingest_file(req: IngestRequest) -> dict:
    """Ingest a single local document into the docs_reference collection."""
    ingestor = _get_ingestor()
    _require_enabled()
    try:
        result = ingestor.ingest_file(req.file_path)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest_file failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error_message", "ingestion error"))
    return result


@router.get("/")
def list_documents(
    limit: int = 20,
    offset: int = 0,
    file_type: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    """List indexed documents with optional filtering."""
    ingestor = _get_ingestor()
    _require_enabled()
    items = ingestor.list_documents(limit=limit, offset=offset, file_type=file_type, status=status)
    # Count total (without pagination)
    all_items = ingestor.list_documents(limit=10_000, offset=0, file_type=file_type, status=status)
    return {
        "items": items,
        "total": len(all_items),
        "limit": limit,
        "offset": offset,
    }


@router.delete("/{doc_id}")
def delete_document(doc_id: str) -> dict:
    """Delete a document from Qdrant and mark it deleted in the log."""
    ingestor = _get_ingestor()
    _require_enabled()
    deleted = ingestor.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"deleted": True, "doc_id": doc_id}


@router.get("/status")
def get_status() -> dict:
    """Return ingestion status summary and per-type breakdown."""
    ingestor = _get_ingestor()
    _require_enabled()
    return ingestor.get_status()
