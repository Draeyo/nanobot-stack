"""Conversation export — Markdown and PDF generation.

Exports conversation histories in structured Markdown format,
with optional PDF conversion if reportlab is available.
"""
from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.export")

EXPORT_ENABLED = os.getenv("EXPORT_ENABLED", "true").lower() == "true"


def export_markdown(
    messages: list[dict[str, str]],
    title: str = "Conversation Export",
    session_id: str = "",
    include_metadata: bool = True,
) -> str:
    """Export a conversation as formatted Markdown."""
    if not EXPORT_ENABLED:
        return ""
    parts = []

    # Header
    parts.append(f"# {title}")
    if include_metadata:
        parts.append("")
        parts.append(f"- **Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        if session_id:
            parts.append(f"- **Session**: {session_id}")
        parts.append(f"- **Messages**: {len(messages)}")
        parts.append("")
        parts.append("---")

    parts.append("")

    # Messages
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "system":
            parts.append("<details><summary>System prompt</summary>")
            parts.append("")
            parts.append(content)
            parts.append("")
            parts.append("</details>")
        elif role == "user":
            parts.append(f"## User")
            parts.append("")
            parts.append(content)
        elif role == "assistant":
            parts.append(f"## Assistant")
            parts.append("")
            parts.append(content)
        else:
            parts.append(f"## {role.title()}")
            parts.append("")
            parts.append(content)

        parts.append("")

    return "\n".join(parts)


def export_structured(
    messages: list[dict[str, str]],
    session_id: str = "",
    summary: str = "",
) -> dict[str, Any]:
    """Export conversation as structured JSON with metadata."""
    user_count = sum(1 for m in messages if m.get("role") == "user")
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")

    # Extract topics (crude: look at user messages)
    all_user_text = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
    # Simple keyword extraction: words > 5 chars appearing multiple times
    words = re.findall(r"\b[a-zA-Z]{5,}\b", all_user_text.lower())
    from collections import Counter
    word_counts = Counter(words)
    topics = [w for w, c in word_counts.most_common(5) if c >= 2]

    return {
        "session_id": session_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(messages),
        "user_messages": user_count,
        "assistant_messages": assistant_count,
        "summary": summary,
        "topics": topics,
        "messages": messages,
    }


def generate_pdf_bytes(markdown_text: str, title: str = "Conversation") -> bytes | None:
    """Generate a PDF from markdown text. Returns bytes or None if reportlab unavailable."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        logger.debug("reportlab not installed, PDF export unavailable")
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    heading_style = styles["Heading2"]
    body_style = styles["BodyText"]

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 12))

    lines = markdown_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], heading_style))
        elif line.startswith("# "):
            pass  # Already used as title
        elif line.startswith("- "):
            story.append(Paragraph(f"• {line[2:]}", body_style))
        elif line == "---":
            story.append(Spacer(1, 12))
        else:
            # Escape HTML entities for reportlab
            safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe_line, body_style))

    doc.build(story)
    return buffer.getvalue()
