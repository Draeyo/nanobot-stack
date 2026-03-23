"""MCP server exposing RAG bridge tools to nanobot.

v11: adds DM pairing management (list_pairing_requests, approve_pairing,
list_approved_channel_users, revoke_channel_user), centralized settings
(list_settings, get_setting), and list_elevated_commands tools.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BRIDGE_URL = os.environ.get("RAG_BRIDGE_URL", "http://127.0.0.1:8089").rstrip("/")
BRIDGE_TOKEN = os.environ.get("RAG_BRIDGE_TOKEN", "")
mcp = FastMCP("rag-bridge")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if BRIDGE_TOKEN:
        h["X-Bridge-Token"] = BRIDGE_TOKEN
    return h


async def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "GET":
            r = await client.get(f"{BRIDGE_URL}{path}", headers=_headers())
        else:
            r = await client.post(f"{BRIDGE_URL}{path}", json=payload or {}, headers=_headers())
        r.raise_for_status()
        return r.json()

async def _post(path, payload=None): return await _call("POST", path, payload)
async def _get(path): return await _call("GET", path)

# ====================== CORE ======================

@mcp.tool()
async def search_memory(query: str, collections: list[str] | None = None, tags: list[str] | None = None, limit: int = 5, source_name: str | None = None) -> str:
    """Search the vector memory for relevant documents and memories. Results include memory decay scoring and feedback boosts."""
    return json.dumps(await _post("/search", {"query": query, "collections": collections or [], "tags": tags or [], "limit": limit, "source_name": source_name}), ensure_ascii=False)

@mcp.tool()
async def remember_memory(text: str, collection: str = "memory_personal", subject: str | None = None, tags: list[str] | None = None, source: str = "nanobot", summarize: bool = True) -> str:
    """Store a durable memory (preference, decision, fact). PII is automatically redacted. Memory type (episodic/semantic) is auto-detected."""
    return json.dumps(await _post("/remember", {"text": text, "collection": collection, "subject": subject, "tags": tags or [], "source": source, "summarize": summarize}), ensure_ascii=False)

@mcp.tool()
async def ask_rag(question: str, collections: list[str] | None = None, tags: list[str] | None = None, limit: int = 6, answer_task: str = "retrieval_answer") -> str:
    """Ask a question grounded in retrieved context (RAG)."""
    return json.dumps(await _post("/ask", {"question": question, "collections": collections or [], "tags": tags or [], "limit": limit, "answer_task": answer_task}), ensure_ascii=False)

@mcp.tool()
async def route_preview(task_type: str) -> str:
    """Preview which model chain will be used for a given task type."""
    return json.dumps(await _post("/route-preview", {"task_type": task_type}), ensure_ascii=False)

@mcp.tool()
async def list_model_routes() -> str:
    """List all configured model routes and profiles."""
    return json.dumps(await _get("/routes"), ensure_ascii=False)

@mcp.tool()
async def rag_health() -> str:
    """Check RAG bridge health including Qdrant and API key status."""
    return json.dumps(await _get("/healthz"), ensure_ascii=False)

# ====================== SMART CHAT v2 ======================

@mcp.tool()
async def smart_chat(
    messages: list[dict[str, str]], auto_classify: bool = True,
    session_id: str = "", enable_hyde: bool = True,
    enable_citations: bool = True, enable_self_critique: bool = True,
) -> str:
    """Smart chat v2: classifies, rewrites with HyDE, detects tone, adds citations, applies self-critique."""
    payload = {
        "messages": messages, "auto_classify": auto_classify,
        "session_id": session_id, "enable_hyde": enable_hyde,
        "enable_citations": enable_citations, "enable_self_critique": enable_self_critique,
    }
    return json.dumps(await _post("/smart-chat", payload), ensure_ascii=False)

# ====================== CLASSIFY ======================

@mcp.tool()
async def classify_query(query: str) -> str:
    """Classify a user query into a task type (memory_lookup, code_task, incident_triage, etc.)."""
    return json.dumps(await _post("/classify", {"query": query}), ensure_ascii=False)

# ====================== QUERY REWRITING ======================

@mcp.tool()
async def rewrite_query(query: str, mode: str = "hyde") -> str:
    """Rewrite a query for better retrieval. Modes: hyde, multi, or both."""
    return json.dumps(
        await _post("/query-rewrite", {"query": query, "mode": mode}), ensure_ascii=False
    )

# ====================== CONVERSATION HOOK ======================

@mcp.tool()
async def conversation_hook(conversation: list[dict[str, str]], extract_facts: bool = True, do_update_profile: bool = True, summarize: bool = True) -> str:
    """Post-conversation: extracts durable facts, updates profile, feeds knowledge graph, stores summary."""
    payload = {
        "conversation": conversation, "extract_facts": extract_facts,
        "update_profile": do_update_profile, "summarize": summarize, "store_summary": True,
    }
    return json.dumps(await _post("/conversation-hook", payload), ensure_ascii=False)

# ====================== CONTEXT PREFETCH ======================

@mcp.tool()
async def context_prefetch(query: str, limit: int = 5, inject_profile: bool = True) -> str:
    """Retrieve relevant context + user profile + knowledge graph relationships for system prompt injection."""
    return json.dumps(await _post("/context-prefetch", {"query": query, "limit": limit, "inject_profile": inject_profile}), ensure_ascii=False)

# ====================== MEMORY COMPACTION ======================

@mcp.tool()
async def compact_memories(subject: str, collection: str = "memory_personal", max_memories: int = 20) -> str:
    """Merge redundant memories about a subject into one consolidated entry."""
    return json.dumps(await _post("/compact", {"subject": subject, "collection": collection, "limit": max_memories}), ensure_ascii=False)

# ====================== PLAN & EXECUTE ======================

@mcp.tool()
async def plan_task(query: str, context: str = "", parallel: bool = True) -> str:
    """Decompose a complex task into a multi-step plan. Independent steps execute in parallel."""
    return json.dumps(await _post("/plan", {"query": query, "context": context, "parallel": parallel}), ensure_ascii=False)

@mcp.tool()
async def execute_step(tool: str, step_input: dict[str, Any] | None = None) -> str:
    """Execute a single step from a plan."""
    return json.dumps(await _post("/execute-step", {"tool": tool, "input": step_input or {}}), ensure_ascii=False)

# ====================== TOOLS ======================

@mcp.tool()
async def run_shell(command: str) -> str:
    """Run a pre-approved read-only shell command (systemctl status, journalctl, curl, dig, df, uptime)."""
    return json.dumps(await _post("/shell", {"command": command}), ensure_ascii=False)

@mcp.tool()
async def fetch_url(url: str) -> str:
    """Fetch a web page and extract its text content."""
    return json.dumps(await _post("/web-fetch", {"url": url}), ensure_ascii=False)

@mcp.tool()
async def notify(message: str, title: str = "nanobot", level: str = "info") -> str:
    """Send a notification via webhook (supports ntfy, Slack, Telegram)."""
    return json.dumps(
        await _post("/notify", {"message": message, "title": title, "level": level}),
        ensure_ascii=False,
    )

# ====================== CODE INTERPRETER ======================

@mcp.tool()
async def execute_code(code: str, timeout: int = 30) -> str:
    """Execute Python code in a secure sandbox. No filesystem/network access."""
    return json.dumps(
        await _post("/code-execute", {"code": code, "timeout": timeout}), ensure_ascii=False
    )

# ====================== KNOWLEDGE GRAPH ======================

@mcp.tool()
async def query_knowledge_graph(entity: str, depth: int = 1) -> str:
    """Query the knowledge graph for an entity and its relationships."""
    return json.dumps(
        await _post("/knowledge-graph/query", {"entity": entity, "depth": depth}),
        ensure_ascii=False,
    )

@mcp.tool()
async def find_relations(entity1: str, entity2: str) -> str:
    """Find all relationships between two entities in the knowledge graph."""
    return json.dumps(
        await _post("/knowledge-graph/relations", {"entity1": entity1, "entity2": entity2}),
        ensure_ascii=False,
    )

# ====================== EXPLAIN ======================

@mcp.tool()
async def explain_query(query: str) -> str:
    """Show full pipeline trace: classification, model routing, search results, tone detection, KG context. Use when user asks 'why this answer?'."""
    return json.dumps(await _post("/explain", {"query": query}), ensure_ascii=False)

# ====================== EXPORT ======================

@mcp.tool()
async def export_conversation(
    messages: list[dict[str, str]], output_format: str = "markdown",
    title: str = "Conversation",
) -> str:
    """Export a conversation as Markdown or structured JSON for archiving."""
    return json.dumps(
        await _post("/export", {"messages": messages, "format": output_format, "title": title}),
        ensure_ascii=False,
    )

# ====================== PII CHECK ======================

@mcp.tool()
async def check_pii(text: str) -> str:
    """Scan text for PII (emails, phones, API keys, SSNs, credit cards). Use before sharing sensitive data."""
    return json.dumps(await _post("/pii-check", {"text": text}), ensure_ascii=False)

# ====================== FEEDBACK ======================

@mcp.tool()
async def give_feedback(chunk_id: str, collection: str, positive: bool) -> str:
    """Give positive/negative feedback on a search result. Improves future ranking via adaptive routing."""
    return json.dumps(await _post("/feedback", {"chunk_id": chunk_id, "collection": collection, "positive": positive}), ensure_ascii=False)

# ====================== PROFILE ======================

@mcp.tool()
async def get_profile() -> str:
    """Get the current user profile (preferences, communication style, expertise)."""
    return json.dumps(await _get("/profile"), ensure_ascii=False)

@mcp.tool()
async def update_profile(updates: dict[str, Any]) -> str:
    """Update user profile fields."""
    return json.dumps(await _post("/profile", updates), ensure_ascii=False)

# ====================== ELEVATED SHELL ======================

@mcp.tool()
async def propose_system_action(command: str, description: str = "") -> str:
    """Propose a mutating system command for user approval (e.g. systemctl restart, apt install, docker run). The command will NOT execute until explicitly approved by the user."""
    return json.dumps(await _post("/actions/propose", {"command": command, "description": description}), ensure_ascii=False)

@mcp.tool()
async def list_pending_actions() -> str:
    """List all pending elevated actions waiting for user approval."""
    return json.dumps(await _get("/actions/pending"), ensure_ascii=False)

# ====================== CONFIG WRITER ======================

@mcp.tool()
async def propose_config_change(file_name: str, content: str, description: str = "") -> str:
    """Propose a configuration change for user approval. Supported files: .env, model_router.json, NANOBOT_POLICY_PROMPT.md. Changes are validated, staged, and diffed for review."""
    return json.dumps(await _post("/config/propose", {"file_name": file_name, "content": content, "description": description}), ensure_ascii=False)

@mcp.tool()
async def list_config_changes() -> str:
    """List all pending configuration changes waiting for user approval."""
    return json.dumps(await _get("/config/pending"), ensure_ascii=False)

# ====================== CHANNELS ======================

@mcp.tool()
async def channel_status() -> str:
    """Check the status of all configured channel adapters (Telegram, Discord, WhatsApp)."""
    return json.dumps(await _get("/channels/status"), ensure_ascii=False)

# ====================== CHANNEL PAIRING ======================

@mcp.tool()
async def list_pairing_requests() -> str:
    """List pending DM pairing requests from channel users awaiting approval."""
    return json.dumps(await _get("/channels/pair/pending"), ensure_ascii=False)

@mcp.tool()
async def approve_pairing(code: str) -> str:
    """Approve a DM pairing code, granting a channel user access to the bot."""
    return json.dumps(await _post(f"/channels/pair/{code}/approve", {}), ensure_ascii=False)

@mcp.tool()
async def list_approved_channel_users() -> str:
    """List all approved channel users (paired users from Telegram, Discord, WhatsApp)."""
    return json.dumps(await _get("/channels/pair/users"), ensure_ascii=False)

@mcp.tool()
async def revoke_channel_user(platform_id: str) -> str:
    """Revoke a channel user's access (e.g. platform_id='telegram:123456')."""
    return json.dumps(await _post("/channels/pair/revoke", {"platform_id": platform_id}), ensure_ascii=False)

# ====================== SETTINGS ======================

@mcp.tool()
async def list_settings(section: str = "") -> str:
    """List all configurable settings, optionally filtered by section. Sections: domain, system, network, models, rag, tools, feedback, elevated_shell, config_writer, channels, ollama."""
    params = f"?section={section}" if section else ""
    return json.dumps(await _get(f"/settings{params}"), ensure_ascii=False)

@mcp.tool()
async def get_setting(key: str) -> str:
    """Get a specific setting by its env var name (e.g. 'RERANKER_ENABLED', 'LOG_LEVEL')."""
    return json.dumps(await _get(f"/settings/key/{key}"), ensure_ascii=False)

@mcp.tool()
async def list_elevated_commands() -> str:
    """List the current elevated shell command allow-list (defaults + user overrides)."""
    return json.dumps(await _get("/actions/commands/list"), ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
