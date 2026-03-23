"""User profile management.

Maintains a JSON profile that personalises the assistant's behaviour:
tone, language, expertise level, preferred response format, etc.
The profile is loaded at startup, injected into context prefetch,
and auto-updated from conversation analysis.
"""
from __future__ import annotations
import json, logging, os, pathlib, threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.user_profile")

PROFILE_DIR = pathlib.Path(os.getenv("DOCS_DIR", "/opt/nanobot-stack/rag-docs")) / "memory"
PROFILE_FILE = PROFILE_DIR / "user_profile.json"
AUTO_UPDATE_ENABLED = os.getenv("PROFILE_AUTO_UPDATE", "true").lower() == "true"
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_mtime: float = 0.0

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "language": "auto",
    "style": "concise and technical",
    "expertise": [],
    "context": "",
    "preferences": {},
    "communication": {
        "tone": "professional",
        "verbosity": "concise",
        "format_preference": "markdown",
        "code_style": {},
    },
    "tool_preferences": {
        "preferred_shell": "bash",
        "default_search_collections": [],
    },
    "schedule": {
        "timezone": "",
        "working_hours": "",
        "notification_preferences": {},
    },
    "learning_log": [],
    "updated_at": "",
}

LEARNING_LOG_MAX = 500

PROFILE_UPDATE_PROMPT = """Given the user profile and conversation, identify any updates needed.
Only update fields where the conversation reveals NEW or CHANGED information.
Return ONLY JSON with the fields to update (omit unchanged fields).

Available fields:
- name, language, style, expertise (list), context, preferences (dict)
- communication: {tone, verbosity (concise|moderate|detailed), format_preference (markdown|plain|structured), code_style}
- tool_preferences: {preferred_shell, default_search_collections}
- schedule: {timezone, working_hours, notification_preferences}

Return {} if no updates needed."""


def load_profile() -> dict[str, Any]:
    global _cache, _cache_mtime
    with _lock:
        try:
            mt = PROFILE_FILE.stat().st_mtime
            if mt != _cache_mtime:
                _cache = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
                _cache_mtime = mt
        except FileNotFoundError:
            if not _cache:
                _cache = dict(DEFAULT_PROFILE)
        except Exception as exc:
            logger.warning("Failed to load profile: %s", exc)
            if not _cache:
                _cache = dict(DEFAULT_PROFILE)
        return dict(_cache)


def save_profile(profile: dict[str, Any]) -> None:
    global _cache, _cache_mtime
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        PROFILE_FILE.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        _cache = dict(profile)
        _cache_mtime = PROFILE_FILE.stat().st_mtime
    logger.info("User profile saved")


def update_profile(updates: dict[str, Any]) -> dict[str, Any]:
    profile = load_profile()
    changed = False
    for key, value in updates.items():
        if key == "learning_log":
            continue  # learning_log is append-only via record_preference_signal
        if key in DEFAULT_PROFILE and value and value != profile.get(key):
            if isinstance(profile.get(key), dict) and isinstance(value, dict):
                # Deep merge for nested dicts (communication, tool_preferences, etc.)
                profile[key] = {**profile.get(key, {}), **value}
            else:
                profile[key] = value
            changed = True
    if changed:
        save_profile(profile)
    return {"updated": changed, "profile": profile}


def record_preference_signal(category: str, key: str, value: Any, confidence: float = 1.0) -> None:
    """Record a preference change in the learning log (append-only, capped)."""
    profile = load_profile()
    log_entry = {
        "category": category,
        "key": key,
        "value": value,
        "confidence": confidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    learning_log = profile.get("learning_log", [])
    learning_log.append(log_entry)
    # Cap at LEARNING_LOG_MAX, keep most recent
    if len(learning_log) > LEARNING_LOG_MAX:
        learning_log = learning_log[-LEARNING_LOG_MAX:]
    profile["learning_log"] = learning_log
    save_profile(profile)


def auto_update_from_conversation(messages: list[dict[str, str]], run_chat_fn) -> dict[str, Any]:
    if not AUTO_UPDATE_ENABLED or not messages:
        return {"updated": False}
    current = load_profile()
    transcript = "\n".join(f"[{m.get('role','user')}] {m.get('content','')[:300]}" for m in messages[-10:])[-2000:]
    try:
        result = run_chat_fn("remember_extract", [
            {"role": "system", "content": PROFILE_UPDATE_PROMPT},
            {"role": "user", "content": f"Current profile:\n{json.dumps(current, ensure_ascii=False)}\n\nConversation:\n{transcript}"},
        ], json_mode=True, max_tokens=400)
        updates = json.loads(result["text"])
        if updates:
            return update_profile(updates)
        return {"updated": False}
    except Exception as exc:
        logger.debug("Auto profile update failed: %s", exc)
        return {"updated": False, "error": "auto profile update failed"}


def format_profile_block(profile: dict[str, Any]) -> str:
    """Format the user profile as a system prompt fragment."""
    if not profile:
        return ""
    parts = []
    if profile.get("name"):
        parts.append(f"User name: {profile['name']}")
    if profile.get("language"):
        parts.append(f"Preferred language: {profile['language']}")
    if profile.get("style"):
        parts.append(f"Response style: {profile['style']}")
    if profile.get("expertise"):
        parts.append(f"Expertise: {', '.join(profile['expertise'])}")
    if profile.get("context"):
        parts.append(f"Context: {profile['context']}")
    if profile.get("preferences"):
        for k, v in profile["preferences"].items():
            parts.append(f"{k}: {v}")
    comm = profile.get("communication", {})
    if comm.get("tone"):
        parts.append(f"Tone: {comm['tone']}")
    if comm.get("verbosity"):
        parts.append(f"Verbosity: {comm['verbosity']}")
    if comm.get("format_preference"):
        parts.append(f"Format: {comm['format_preference']}")
    sched = profile.get("schedule", {})
    if sched.get("timezone"):
        parts.append(f"Timezone: {sched['timezone']}")
    if not parts:
        return ""
    return "## User profile\n" + "\n".join(f"- {p}" for p in parts)
