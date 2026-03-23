"""Centralized settings registry — one place to view, describe, and modify all parameters.

Every configurable env var is registered here with its current value, default,
description, and section.  The admin can:
  GET  /settings            — list all settings grouped by section
  GET  /settings/{key}      — get one setting with metadata
  POST /settings/{key}      — propose a change (routed through config_writer)

Changing a setting creates a config_writer proposal for the .env file so that
all changes go through the approval workflow.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.settings")

router = APIRouter(prefix="/settings", tags=["settings"])
_verify_token = None


def init_settings(verify_token_dep=None):
    global _verify_token
    _verify_token = verify_token_dep


# ---------------------------------------------------------------------------
# Setting definition
# ---------------------------------------------------------------------------
@dataclass
class SettingDef:
    key: str                       # env var name
    default: str                   # default value
    description: str               # human-readable description
    section: str                   # logical group
    sensitive: bool = False        # mask value in API output
    choices: list[str] = field(default_factory=list)  # valid values (empty = freeform)


# ---------------------------------------------------------------------------
# Registry — all configurable env vars
# ---------------------------------------------------------------------------
_SETTINGS: list[SettingDef] = [
    # --- Domain & subdomains ---
    SettingDef("DOMAIN", "example.com", "Base domain for all services", "domain"),
    SettingDef("NANOBOT_SUBDOMAIN", "ai", "Subdomain for nanobot agent", "domain"),
    SettingDef("RAG_SUBDOMAIN", "rag", "Subdomain for RAG bridge", "domain"),
    SettingDef("LANGFUSE_SUBDOMAIN", "observability", "Subdomain for Langfuse", "domain"),
    SettingDef("WEBUI_SUBDOMAIN", "chat", "Subdomain for web UI", "domain"),
    SettingDef("AUTHENTIK_OUTPOST_FQDN", "auth.example.com", "Authentik outpost FQDN", "domain"),

    # --- System ---
    SettingDef("APP_USER", "nanobot", "System user running the stack", "system"),
    SettingDef("APP_GROUP", "nanobot", "System group running the stack", "system"),
    SettingDef("BASE_DIR", "/opt/nanobot-stack", "Base installation directory", "system"),
    SettingDef("LOG_LEVEL", "INFO", "Logging level", "system", choices=["DEBUG", "INFO", "WARNING", "ERROR"]),

    # --- Bind addresses & ports ---
    SettingDef("NANOBOT_BIND", "127.0.0.1", "Nanobot agent bind address", "network"),
    SettingDef("NANOBOT_PORT", "18790", "Nanobot agent port", "network"),
    SettingDef("RAG_BIND", "127.0.0.1", "RAG bridge bind address", "network"),
    SettingDef("RAG_PORT", "8089", "RAG bridge port", "network"),
    SettingDef("QDRANT_BIND", "127.0.0.1", "Qdrant bind address", "network"),
    SettingDef("QDRANT_HTTP_PORT", "6333", "Qdrant HTTP port", "network"),
    SettingDef("QDRANT_GRPC_PORT", "6334", "Qdrant gRPC port", "network"),
    SettingDef("LANGFUSE_BIND", "127.0.0.1", "Langfuse bind address", "network"),
    SettingDef("LANGFUSE_WEB_PORT", "3300", "Langfuse web port", "network"),
    SettingDef("WEBUI_BIND", "127.0.0.1", "WebUI bind address", "network"),
    SettingDef("WEBUI_PORT", "18800", "WebUI port", "network"),

    # --- Models ---
    SettingDef("NANOBOT_DEFAULT_MODEL", "anthropic/claude-sonnet-4-20250514", "Default LLM model for the agent", "models"),

    # --- RAG tuning ---
    SettingDef("RERANKER_ENABLED", "true", "Enable cross-encoder reranker", "rag", choices=["true", "false"]),
    SettingDef("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3", "Reranker model name", "rag"),
    SettingDef("RERANKER_DEVICE", "cpu", "Reranker device", "rag", choices=["cpu", "cuda"]),
    SettingDef("SPARSE_VECTORS_ENABLED", "true", "Enable hybrid sparse+dense search", "rag", choices=["true", "false"]),
    SettingDef("EMBEDDING_CACHE_SIZE", "512", "Embedding cache size", "rag"),
    SettingDef("EMBEDDING_CACHE_TTL", "3600", "Embedding cache TTL (seconds)", "rag"),
    SettingDef("EMBEDDING_BATCH_SIZE", "32", "Embedding batch size", "rag"),
    SettingDef("MAX_CHUNK_CHARS", "1800", "Max characters per chunk", "rag"),
    SettingDef("CHUNK_OVERLAP", "200", "Chunk overlap characters", "rag"),
    SettingDef("SEARCH_LIMIT", "8", "Default search result limit", "rag"),
    SettingDef("PREFETCH_MULTIPLIER", "4", "Prefetch multiplier for search", "rag"),
    SettingDef("MAX_PREFETCH", "24", "Maximum prefetch count", "rag"),
    SettingDef("AUTO_SUMMARIZE_MEMORY", "true", "Auto-summarize stored memories", "rag", choices=["true", "false"]),
    SettingDef("DEFAULT_ANSWER_TASK", "retrieval_answer", "Default task type for /ask", "rag"),

    # --- Rate limiting ---
    SettingDef("REMEMBER_RATE_CAPACITY", "30", "Rate limiter burst capacity for /remember", "rate_limiting"),
    SettingDef("REMEMBER_RATE_REFILL", "0.5", "Rate limiter refill rate (tokens/sec)", "rate_limiting"),

    # --- Circuit breaker ---
    SettingDef("CB_FAILURE_THRESHOLD", "3", "Failures before circuit opens", "circuit_breaker"),
    SettingDef("CB_RECOVERY_TIMEOUT", "120", "Circuit breaker recovery timeout (seconds)", "circuit_breaker"),

    # --- Vision ---
    SettingDef("VISION_ENABLED", "true", "Enable vision/multi-modal ingestion", "vision", choices=["true", "false"]),
    SettingDef("VISION_MAX_IMAGES_PER_DOC", "5", "Max images extracted per document", "vision"),
    SettingDef("VISION_MIN_IMAGE_BYTES", "5000", "Minimum image size to process (bytes)", "vision"),

    # -- Tools ---
    SettingDef("SHELL_TIMEOUT", "15", "Read-only shell command timeout (seconds)", "tools"),
    SettingDef("WEB_FETCH_TIMEOUT", "30", "Web fetch timeout (seconds)", "tools"),
    SettingDef("WEB_FETCH_MAX_CHARS", "15000", "Web fetch max response characters", "tools"),
    SettingDef("NOTIFICATION_WEBHOOK_URL", "", "Notification webhook URL (ntfy, Slack, etc.)", "tools", sensitive=True),

    # --- Feedback ---
    SettingDef("FEEDBACK_BOOST_WEIGHT", "0.1", "Weight per feedback event", "feedback"),
    SettingDef("FEEDBACK_MAX_BOOST", "0.5", "Maximum positive boost", "feedback"),
    SettingDef("FEEDBACK_MIN_BOOST", "-0.3", "Maximum negative boost", "feedback"),

    # --- Elevated shell ---
    SettingDef("ELEVATED_SHELL_ENABLED", "false", "Enable approval-gated mutating shell commands", "elevated_shell", choices=["true", "false"]),
    SettingDef("ELEVATED_SHELL_TIMEOUT", "60", "Elevated command execution timeout (seconds)", "elevated_shell"),
    SettingDef("ELEVATED_ACTION_EXPIRY", "30", "Minutes until pending action expires", "elevated_shell"),
    SettingDef("ELEVATED_EXTRA_COMMANDS", "", "JSON object of extra commands to add (e.g. '{\"npm\": [\"install\"], \"snap\": true}')", "elevated_shell"),
    SettingDef("ELEVATED_DISABLED_COMMANDS", "", "Comma-separated binaries to remove from defaults (e.g. 'rm,chmod')", "elevated_shell"),

    # --- Config writer ---
    SettingDef("CONFIG_WRITER_ENABLED", "false", "Enable approval-gated configuration changes", "config_writer", choices=["true", "false"]),
    SettingDef("CONFIG_CHANGE_EXPIRY", "60", "Minutes until pending config change expires", "config_writer"),

    # --- Channel adapters ---
    SettingDef("CHANNELS_ENABLED", "true", "Enable channel adapter system", "channels", choices=["true", "false"]),
    SettingDef("CHANNEL_DM_POLICY", "pairing", "DM policy for all channels: 'pairing' (require approval) or 'open'", "channels", choices=["pairing", "open"]),
    SettingDef("TELEGRAM_BOT_TOKEN", "", "Telegram bot token from @BotFather", "channels", sensitive=True),
    SettingDef("TELEGRAM_ALLOWED_CHAT_IDS", "", "Comma-separated allowed Telegram chat IDs (empty = pairing-based)", "channels"),
    SettingDef("DISCORD_BOT_TOKEN", "", "Discord bot token", "channels", sensitive=True),
    SettingDef("DISCORD_ALLOWED_CHANNEL_IDS", "", "Comma-separated allowed Discord channel IDs (empty = pairing-based)", "channels"),
    SettingDef("WHATSAPP_ACCESS_TOKEN", "", "WhatsApp Business API access token", "channels", sensitive=True),
    SettingDef("WHATSAPP_PHONE_NUMBER_ID", "", "WhatsApp phone number ID", "channels"),
    SettingDef("WHATSAPP_VERIFY_TOKEN", "", "WhatsApp webhook verification token", "channels", sensitive=True),
    SettingDef("WHATSAPP_APP_SECRET", "", "WhatsApp app secret for signature verification", "channels", sensitive=True),

    # --- Ollama ---
    SettingDef("INSTALL_OLLAMA", "true", "Install Ollama as local LLM fallback", "ollama", choices=["true", "false"]),
    SettingDef("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1", "Ollama API base URL", "ollama"),
    SettingDef("OLLAMA_API_KEY", "ollama", "Ollama API key", "ollama", sensitive=True),
    SettingDef("OLLAMA_CHAT_MODEL", "qwen2.5:7b", "Ollama chat model", "ollama"),
    SettingDef("OLLAMA_EMBED_MODEL", "nomic-embed-text", "Ollama embedding model", "ollama"),

    # --- Trust Engine (v10) ---
    SettingDef("TRUST_ENGINE_ENABLED", "true", "Enable per-action trust level gating", "trust", choices=["true", "false"]),
    SettingDef("TRUST_DEFAULT_LEVEL", "approval_required", "Default trust level for unconfigured actions",
               "trust", choices=["auto", "notify_then_execute", "approval_required", "blocked"]),
    SettingDef("TRUST_AUTO_PROMOTE_THRESHOLD", "20", "Consecutive successes before auto-promoting trust level", "trust"),
    SettingDef("TRUST_NOTIFY_CHANNEL", "", "Webhook URL for trust notifications", "trust"),
    SettingDef("TRUST_ROLLBACK_WINDOW_HOURS", "24", "Hours within which rollback is available", "trust"),

    # --- Procedural Memory (v10) ---
    SettingDef("PROCEDURAL_MEMORY_ENABLED", "false", "Enable workflow learning from action patterns", "procedural_memory", choices=["true", "false"]),
    SettingDef("PROCEDURAL_DETECT_THRESHOLD", "10", "Minimum new actions before pattern detection runs", "procedural_memory"),
    SettingDef("PROCEDURAL_SCAN_WINDOW", "100", "Max actions to scan for patterns", "procedural_memory"),
    SettingDef("PROCEDURAL_SUGGEST_CONFIDENCE", "0.7", "Minimum confidence to suggest a workflow", "procedural_memory"),

    # --- Agent Orchestrator (v10) ---
    SettingDef("AGENT_ORCHESTRATOR_ENABLED", "false", "Enable multi-agent orchestration", "agents", choices=["true", "false"]),

    # --- Semantic Cache (v10) ---
    SettingDef("SEMANTIC_CACHE_ENABLED", "false", "Enable vector-similarity LLM response cache", "semantic_cache", choices=["true", "false"]),
    SettingDef("SEMANTIC_CACHE_THRESHOLD", "0.92", "Cosine similarity threshold for cache hits", "semantic_cache"),
    SettingDef("SEMANTIC_CACHE_TTL", "86400", "Cache TTL in seconds (default 24h)", "semantic_cache"),
    SettingDef("SEMANTIC_CACHE_MAX_SIZE", "1000", "Max entries in semantic cache", "semantic_cache"),

    # --- Token Budget (v10) ---
    SettingDef("TOKEN_BUDGET_ENABLED", "false", "Enable token/cost budget tracking", "token_budget", choices=["true", "false"]),
    SettingDef("DAILY_TOKEN_BUDGET", "5000000", "Daily token budget (default 5M)", "token_budget"),
    SettingDef("DAILY_COST_BUDGET_CENTS", "300", "Daily cost budget in cents (default $3)", "token_budget"),
]

_SETTINGS_BY_KEY: dict[str, SettingDef] = {s.key: s for s in _SETTINGS}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_all_settings() -> list[dict[str, Any]]:
    """Return all settings grouped by section."""
    result = []
    for s in _SETTINGS:
        current = os.getenv(s.key, str(s.default))  # pylint: disable=invalid-envvar-value
        result.append({
            "key": s.key,
            "value": "********" if s.sensitive and current else current,
            "default": s.default,
            "description": s.description,
            "section": s.section,
            "sensitive": s.sensitive,
            "choices": s.choices or None,
        })
    return result


def get_setting(key: str) -> dict[str, Any] | None:
    s = _SETTINGS_BY_KEY.get(key)
    if not s:
        return None
    current = os.getenv(s.key, str(s.default))
    return {
        "key": s.key,
        "value": "********" if s.sensitive and current else current,
        "default": s.default,
        "description": s.description,
        "section": s.section,
        "sensitive": s.sensitive,
        "choices": s.choices or None,
    }


def get_sections() -> dict[str, list[dict[str, Any]]]:
    """Return settings grouped by section."""
    sections: dict[str, list[dict[str, Any]]] = {}
    for item in get_all_settings():
        sections.setdefault(item["section"], []).append(item)
    return sections


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------

class UpdateSettingIn(BaseModel):
    value: str
    description: str = ""


@router.get("")
def list_settings_endpoint(request: Request, section: str = ""):
    """List all settings, optionally filtered by section."""
    if _verify_token:
        _verify_token(request)
    if section:
        all_s = get_all_settings()
        return {"settings": [s for s in all_s if s["section"] == section]}
    return {"settings": get_all_settings()}


@router.get("/sections")
def list_sections_endpoint(request: Request):
    """List all settings grouped by section."""
    if _verify_token:
        _verify_token(request)
    return {"sections": get_sections()}


@router.get("/key/{key}")
def get_setting_endpoint(key: str, request: Request):
    """Get a single setting by its env var name."""
    if _verify_token:
        _verify_token(request)
    setting = get_setting(key)
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return setting


@router.post("/key/{key}")
def update_setting_endpoint(key: str, body: UpdateSettingIn, request: Request):
    """Propose a setting change via the config writer approval workflow.

    This reads the current .env, updates the key, and routes through
    config_writer.propose_config_change so the change is staged, diffed,
    and requires explicit approval.
    """
    if _verify_token:
        _verify_token(request)

    setting = _SETTINGS_BY_KEY.get(key)
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")

    # Validate choices
    if setting.choices and body.value not in setting.choices:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid value '{body.value}'. Allowed: {setting.choices}"
        )

    # Route through config writer
    try:
        from config_writer import CONFIG_WRITER_ENABLED, propose_config_change
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="Config writer module not available") from exc

    if not CONFIG_WRITER_ENABLED:
        raise HTTPException(status_code=409, detail="Config writer is disabled. "
                            "Enable it with CONFIG_WRITER_ENABLED=true to change settings via API.")

    # Read current .env, update or add the key
    import pathlib
    env_path = pathlib.Path(os.getenv("RAG_HOME", "/opt/nanobot-stack/rag-bridge")) / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                lines.append(f'{key}="{body.value}"')
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f'{key}="{body.value}"')

    new_content = "\n".join(lines) + "\n"
    desc = body.description or f"Update {key} to '{body.value}'"

    result = propose_config_change(".env", new_content, desc)
    return result
