"""Active conversational memory pipeline.

- extract_facts: after each exchange, auto-extract durable facts
- context_prefetch: before each response, inject relevant memories
- summarize_conversation: structured summaries of full sessions
- compact_memories: merge redundant memories on the same subject
"""
from __future__ import annotations
import json, logging, os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.conv_memory")
AUTO_EXTRACT_ENABLED = os.getenv("AUTO_EXTRACT_ENABLED", "true").lower() == "true"
PREFETCH_ENABLED = os.getenv("PREFETCH_ENABLED", "true").lower() == "true"
PREFETCH_LIMIT = int(os.getenv("PREFETCH_LIMIT", "5"))
COMPACTION_THRESHOLD = int(os.getenv("COMPACTION_THRESHOLD", "10"))

EXTRACT_PROMPT = """Analyse this conversation and extract durable facts worth remembering.
Focus on: decisions made, preferences expressed, project context, people mentioned,
technical choices, deadlines, action items.
Do NOT extract: transient troubleshooting, greetings, small talk, questions the user
was merely asking about (only extract stated facts about themselves/their projects).

Return ONLY JSON:
{"facts": [{"text": "concise fact", "subject": "topic", "tags": ["t1"]}],
 "conversation_summary": "2-3 sentence summary",
 "action_items": ["action 1"]}
Return {"facts": [], "conversation_summary": "", "action_items": []} if nothing memorable."""

COMPACTION_PROMPT = """Merge these memory entries about the same subject into ONE concise entry.
Remove duplicates; keep the most recent info when contradictions exist.
Return ONLY JSON: {"merged_text": "consolidated memory", "tags": ["t1"]}"""

SUMMARY_PROMPT = """Summarise this conversation as a structured document:
- Subject: one-line topic
- Key points: important points discussed
- Decisions: decisions made
- Action items: follow-ups needed
- Context: relevant background for future reference
Concise but complete. This will be searchable later."""


def extract_facts(messages: list[dict[str, str]], run_chat_fn) -> dict[str, Any]:
    if not AUTO_EXTRACT_ENABLED or not messages:
        return {"facts": [], "conversation_summary": "", "action_items": []}
    parts = []
    for msg in messages[-20:]:
        parts.append(f"[{msg.get('role','user')}] {msg.get('content','')[:500]}")
    transcript = "\n".join(parts)[-3000:]
    try:
        result = run_chat_fn("remember_extract", [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": transcript},
        ], json_mode=True, max_tokens=800)
        data = json.loads(result["text"])
        return {"facts": data.get("facts", []), "conversation_summary": data.get("conversation_summary", ""),
                "action_items": data.get("action_items", []), "extract_attempts": result.get("attempts", [])}
    except Exception as exc:
        logger.warning("Fact extraction failed: %s", exc)
        return {"facts": [], "conversation_summary": "", "action_items": [], "error": str(exc)}


def build_context_prefetch(query: str, search_fn, user_profile: dict[str, Any] | None = None) -> str:
    if not PREFETCH_ENABLED:
        return ""
    parts = []
    if user_profile:
        lines = []
        for k in ("name", "language", "style", "expertise", "context"):
            v = user_profile.get(k)
            if v:
                lines.append(f"  {k}: {v if isinstance(v, str) else ', '.join(v)}")
        if lines:
            parts.append("## User profile\n" + "\n".join(lines))
    try:
        results = search_fn(query, ["memory_personal", "memory_projects"], limit=PREFETCH_LIMIT)
        if results:
            mem_lines = []
            for item in results[:PREFETCH_LIMIT]:
                text = item.get("payload", {}).get("text", "")[:300]
                if text:
                    mem_lines.append(f"- {text}")
            if mem_lines:
                parts.append("## Relevant memories\n" + "\n".join(mem_lines))
    except Exception as exc:
        logger.debug("Prefetch search failed: %s", exc)
    return ("\n\n".join(parts) + "\n") if parts else ""


def summarize_conversation(messages: list[dict[str, str]], run_chat_fn, session_id: str = "") -> dict[str, Any]:
    if not messages:
        return {"summary": "", "session_id": session_id}
    parts = [f"[{m.get('role','user')}] {m.get('content','')[:800]}" for m in messages[-50:]]
    transcript = "\n".join(parts)[-6000:]
    try:
        result = run_chat_fn("conversation_summary", [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ], max_tokens=1200)
        return {"summary": result["text"], "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(), "message_count": len(messages)}
    except Exception as exc:
        logger.warning("Summarization failed: %s", exc)
        return {"summary": "", "session_id": session_id, "error": str(exc)}


def compact_memories(subject: str, memories: list[dict[str, Any]], run_chat_fn) -> dict[str, Any]:
    if len(memories) < 2:
        return {"compacted": False, "reason": "fewer than 2 memories"}
    all_tags: set[str] = set()
    lines = []
    for i, mem in enumerate(memories[:20]):
        text = mem.get("text", mem.get("payload", {}).get("text", ""))
        tags = mem.get("tags", mem.get("payload", {}).get("tags", []))
        created = mem.get("created_at", mem.get("payload", {}).get("created_at", ""))
        lines.append(f"[{i+1}] ({created}) {text}")
        all_tags.update(tags)
    try:
        result = run_chat_fn("memory_summary", [
            {"role": "system", "content": COMPACTION_PROMPT},
            {"role": "user", "content": f"Subject: {subject}\n\n" + "\n".join(lines)},
        ], json_mode=True, max_tokens=600)
        data = json.loads(result["text"])
        return {"compacted": True, "merged_text": data.get("merged_text", ""),
                "tags": sorted(set(data.get("tags", [])) | all_tags), "source_count": len(memories)}
    except Exception as exc:
        logger.warning("Compaction failed: %s", exc)
        return {"compacted": False, "error": str(exc)}
