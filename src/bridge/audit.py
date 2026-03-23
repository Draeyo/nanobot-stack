"""Append-only audit log middleware for the RAG bridge.

Logs every authenticated request to a JSONL file with:
  timestamp, method, path, source IP, token hash (first 8 chars),
  status code, and response time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("rag-bridge.audit")

AUDIT_LOG_PATH = pathlib.Path(
    os.getenv("AUDIT_LOG_PATH", "/opt/nanobot-stack/rag-bridge/state/audit.jsonl")
)


def _token_fingerprint(token: str) -> str:
    """Return the first 8 hex chars of the SHA-256 of the token (non-reversible)."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode()).hexdigest()[:8]


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        # Skip noisy health checks from audit log
        if request.url.path in ("/healthz", "/metrics"):
            return response

        token = request.headers.get("X-Bridge-Token", "")
        client_ip = request.client.host if request.client else "unknown"

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ip": client_ip,
            "token_fp": _token_fingerprint(token),
            "ms": elapsed_ms,
        }

        try:
            AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed to write audit log: %s", exc)

        return response


def log_audit_event(event_type: str, data: dict) -> None:
    """Write a structured audit record outside of HTTP middleware context."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write audit event: %s", exc)
