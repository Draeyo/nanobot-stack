"""Automatic query classification into task_types.

Uses router_fast (~100ms) to classify before routing, so the bridge picks
the cheapest appropriate model chain instead of always falling to premium.
"""
from __future__ import annotations
import json, logging, os
from typing import Any

logger = logging.getLogger("rag-bridge.classifier")
CLASSIFIER_ENABLED = os.getenv("CLASSIFIER_ENABLED", "true").lower() == "true"
CLASSIFIER_TASK = os.getenv("CLASSIFIER_TASK", "classify_query")

CLASSIFICATION_PROMPT = """Classify the user message into exactly ONE category.
Return ONLY a JSON object: {"task_type": "<category>", "needs_retrieval": true/false, "confidence": 0.0-1.0}

Categories:
- memory_lookup: asks about something discussed before, a preference, a past decision
- factual_question: asks a factual question answerable from documents/runbooks
- code_task: write, review, debug, or explain code
- incident_triage: reports an issue, outage, error, or alert
- creative_writing: write prose, draft an email, summarise
- translation: translate text between languages
- structured_extraction: extract data, parse, convert formats
- planning: plan, organise, decompose a task
- general_chat: casual conversation, greetings, opinions, anything else

needs_retrieval: true if answering likely requires documents, memories, or past conversations."""

TASK_TYPE_MAP = {
    "memory_lookup": "retrieval_answer",
    "factual_question": "retrieval_answer",
    "code_task": "code_reasoning",
    "incident_triage": "incident_triage",
    "creative_writing": "rewrite_polish",
    "translation": "translation",
    "structured_extraction": "structured_extraction",
    "planning": "tool_planning",
    "general_chat": "fallback_general",
}

def classify_query(query: str, run_chat_fn) -> dict[str, Any]:
    if not CLASSIFIER_ENABLED:
        return {"task_type": "fallback_general", "needs_retrieval": True, "confidence": 0.0, "raw_category": "disabled", "classifier_used": False}
    try:
        result = run_chat_fn(CLASSIFIER_TASK, [
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user", "content": query[:2000]},
        ], json_mode=True, max_tokens=150)
        data = json.loads(result["text"])
        raw = data.get("task_type", "general_chat")
        needs = data.get("needs_retrieval", True)
        conf = float(data.get("confidence", 0.5))
        task_type = TASK_TYPE_MAP.get(raw, "fallback_general")
        if conf < 0.4:
            task_type = "fallback_general"; needs = True
        return {"task_type": task_type, "needs_retrieval": needs, "confidence": conf, "raw_category": raw, "classifier_used": True, "classifier_attempts": result.get("attempts", [])}
    except Exception as exc:
        logger.warning("Classification failed: %s", exc)
        return {"task_type": "fallback_general", "needs_retrieval": True, "confidence": 0.0, "raw_category": "error", "classifier_used": False, "error": str(exc)}
