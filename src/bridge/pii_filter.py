"""PII (Personally Identifiable Information) filtering.

Detects and redacts PII from text before storage or response.
Uses regex patterns for common PII types — no external dependencies.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("rag-bridge.pii_filter")

PII_FILTER_ENABLED = os.getenv("PII_FILTER_ENABLED", "true").lower() == "true"
PII_FILTER_ON_INGEST = os.getenv("PII_FILTER_ON_INGEST", "true").lower() == "true"
PII_FILTER_ON_RESPONSE = os.getenv("PII_FILTER_ON_RESPONSE", "false").lower() == "true"
PII_REDACTION_MARKER = os.getenv("PII_REDACTION_MARKER", "[REDACTED]")

# Pattern definitions: (name, compiled_regex, replacement)
_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), f"{PII_REDACTION_MARKER}:email"),
    ("phone_intl", re.compile(r"\+\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}"), f"{PII_REDACTION_MARKER}:phone"),
    ("phone_us", re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), f"{PII_REDACTION_MARKER}:phone"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), f"{PII_REDACTION_MARKER}:ssn"),
    ("credit_card", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), f"{PII_REDACTION_MARKER}:cc"),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), f"{PII_REDACTION_MARKER}:ip"),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), f"{PII_REDACTION_MARKER}:aws_key"),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"), f"{PII_REDACTION_MARKER}:private_key"),
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_.+/=]+\b"), f"{PII_REDACTION_MARKER}:jwt"),
    ("api_key_generic", re.compile(r"\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+=]{16,}['\"]?", re.IGNORECASE), f"{PII_REDACTION_MARKER}:api_key"),
]


def scan_pii(text: str) -> list[dict[str, Any]]:
    """Scan text for PII patterns. Returns list of detections."""
    if not PII_FILTER_ENABLED or not text:
        return []

    detections = []
    for name, pattern, _replacement in _PII_PATTERNS:
        matches = pattern.finditer(text)
        for match in matches:
            detections.append({
                "type": name,
                "start": match.start(),
                "end": match.end(),
                "preview": text[max(0, match.start() - 10):match.start()] + "***" + text[match.end():match.end() + 10],
            })
    return detections


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact PII from text. Returns (cleaned_text, list_of_pii_types_found)."""
    if not PII_FILTER_ENABLED or not text:
        return text, []

    found_types: list[str] = []
    result = text

    for name, pattern, replacement in _PII_PATTERNS:
        new_result = pattern.sub(replacement, result)
        if new_result != result:
            found_types.append(name)
            result = new_result

    if found_types:
        logger.info("PII redacted: %s", ", ".join(found_types))

    return result, found_types


def redact_for_ingest(text: str) -> tuple[str, list[str]]:
    """Redact PII from text before ingestion into the vector store."""
    if not PII_FILTER_ON_INGEST:
        return text, []
    return redact_pii(text)


def redact_for_response(text: str) -> tuple[str, list[str]]:
    """Redact PII from text before sending to the user."""
    if not PII_FILTER_ON_RESPONSE:
        return text, []
    return redact_pii(text)


def check_text(text: str) -> dict[str, Any]:
    """Check text for PII without redacting. Returns a report."""
    detections = scan_pii(text)
    return {
        "has_pii": len(detections) > 0,
        "detection_count": len(detections),
        "types_found": list({d["type"] for d in detections}),
        "detections": detections[:20],  # Limit output
    }
