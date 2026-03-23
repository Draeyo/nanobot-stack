"""Sentiment and tone detection for adaptive response style.

Detects the emotional tone and urgency of user messages and adjusts
the response style dynamically per session (beyond the static user_profile).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("rag-bridge.sentiment")

SENTIMENT_ENABLED = os.getenv("SENTIMENT_ENABLED", "true").lower() == "true"

# Heuristic keyword patterns (fast, no LLM call needed)
_URGENT_PATTERNS = re.compile(
    r"\b(urgent|asap|emergency|down|outage|broken|critical|crash|fail|error|help!|blocker|incident)\b",
    re.IGNORECASE,
)
_FRUSTRATED_PATTERNS = re.compile(
    r"\b(still|again|doesn'?t work|not working|why|impossible|annoying|frustrated|stuck|waste)\b",
    re.IGNORECASE,
)
_CASUAL_PATTERNS = re.compile(
    r"\b(hey|hi|hello|thanks|btw|lol|haha|cool|nice|awesome|great)\b",
    re.IGNORECASE,
)
_FORMAL_PATTERNS = re.compile(
    r"\b(please|kindly|could you|would you|regarding|pursuant|hereby|sincerely)\b",
    re.IGNORECASE,
)


def detect_tone(message: str) -> dict[str, Any]:
    """Detect the tone and urgency of a message using heuristics.

    Returns a dict with:
    - tone: 'urgent', 'frustrated', 'casual', 'formal', 'neutral'
    - urgency: float 0.0-1.0
    - style_hint: suggested response style adjustment
    """
    if not SENTIMENT_ENABLED or not message:
        return {"tone": "neutral", "urgency": 0.3, "style_hint": ""}

    scores = {
        "urgent": len(_URGENT_PATTERNS.findall(message)),
        "frustrated": len(_FRUSTRATED_PATTERNS.findall(message)),
        "casual": len(_CASUAL_PATTERNS.findall(message)),
        "formal": len(_FORMAL_PATTERNS.findall(message)),
    }

    # Check for exclamation marks and caps as urgency signals
    excl_count = message.count("!")
    caps_ratio = sum(1 for c in message if c.isupper()) / max(1, len(message))
    if excl_count > 2:
        scores["urgent"] += excl_count - 1
    if caps_ratio > 0.5 and len(message) > 10:
        scores["urgent"] += 2
        scores["frustrated"] += 1

    # Question marks with no other signals = neutral/inquisitive
    q_count = message.count("?")

    top_tone = max(scores, key=lambda k: scores[k])
    top_score = scores[top_tone]

    if top_score == 0:
        tone = "neutral"
        urgency = 0.3
    else:
        tone = top_tone
        urgency = min(1.0, 0.3 + top_score * 0.15)

    # Style hints based on detected tone
    style_hints = {
        "urgent": "Be direct and action-oriented. Skip pleasantries. Prioritize the fix.",
        "frustrated": "Be empathetic and patient. Acknowledge the difficulty. Offer clear steps.",
        "casual": "Be friendly and conversational. Keep it light.",
        "formal": "Be precise and professional. Use structured responses.",
        "neutral": "",
    }

    return {
        "tone": tone,
        "urgency": round(urgency, 2),
        "style_hint": style_hints.get(tone, ""),
        "scores": scores,
    }


def build_tone_system_prompt(tone_info: dict[str, Any]) -> str:
    """Build a system prompt fragment for tone adaptation."""
    hint = tone_info.get("style_hint", "")
    if not hint:
        return ""
    tone = tone_info.get("tone", "neutral")
    urgency = tone_info.get("urgency", 0.3)
    parts = [f"## Detected conversation tone: {tone} (urgency: {urgency})"]
    parts.append(hint)
    return "\n".join(parts)


def detect_session_tone(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Detect the overall tone from recent messages in a session."""
    if not SENTIMENT_ENABLED or not messages:
        return {"tone": "neutral", "urgency": 0.3, "style_hint": ""}

    # Analyze last 5 user messages
    user_msgs = [m["content"] for m in messages if m.get("role") == "user"][-5:]
    if not user_msgs:
        return {"tone": "neutral", "urgency": 0.3, "style_hint": ""}

    combined = " ".join(user_msgs)
    return detect_tone(combined)
