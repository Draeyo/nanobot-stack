"""v8 extension endpoints — mounted onto the main FastAPI app.

Adds: /classify, /conversation-hook, /context-prefetch, /summarize-conversation,
/compact, /compact-memories, /plan, /execute-step, /smart-chat, /shell,
/web-fetch, /notify, /feedback, /feedback-stats, /profile, /dashboard
"""
from __future__ import annotations
import json, logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.extensions")
router = APIRouter()

# These get set by the main app at startup via init_extensions()
_run_chat_fn = None
_search_fn = None
_remember_fn = None
_verify_token = None
_embed_fn_adapter = None


def init_extensions(run_chat_fn, search_fn, remember_fn, verify_token_dep, embed_fn=None):
    global _run_chat_fn, _search_fn, _remember_fn, _verify_token, _embed_fn_adapter
    _run_chat_fn = run_chat_fn
    _search_fn = search_fn
    _remember_fn = remember_fn
    _verify_token = verify_token_dep
    _embed_fn_adapter = embed_fn


def _auth():
    return [Depends(_verify_token)] if _verify_token else []

# --------------------------------------------------------------------------
# Query classification
# --------------------------------------------------------------------------
class ClassifyIn(BaseModel):
    query: str

@router.post("/classify")
def classify_endpoint(body: ClassifyIn, request: Request):
    if _verify_token: _verify_token(request)
    from query_classifier import classify_query
    return classify_query(body.query, _run_chat_fn)

# --------------------------------------------------------------------------
# Conversation hook (post-conversation fact extraction + profile update)
# --------------------------------------------------------------------------
class ConversationHookIn(BaseModel):
    messages: list[dict[str, str]]
    session_id: str = ""
    auto_remember: bool = True

@router.post("/conversation-hook")
def conversation_hook(body: ConversationHookIn, request: Request):
    if _verify_token: _verify_token(request)
    from token_optimizer import build_merged_extract_messages
    from user_profile import update_profile

    results: dict[str, Any] = {"session_id": body.session_id}

    # Single merged LLM call: extract facts + profile updates together (saves 1 LLM call)
    try:
        merged_msgs = build_merged_extract_messages(body.messages)
        resp = _run_chat_fn("remember_extract", merged_msgs, json_mode=True, max_tokens=1200)
        data = json.loads(resp.get("text", "{}"))
    except Exception as e:
        results["error"] = str(e)
        return results

    extraction = {
        "facts": data.get("facts", []),
        "conversation_summary": data.get("conversation_summary", ""),
        "decisions": data.get("decisions", []),
        "action_items": data.get("action_items", []),
    }
    results["extraction"] = extraction

    # Auto-remember extracted facts
    if body.auto_remember and extraction.get("facts"):
        stored = []
        for fact in extraction["facts"]:
            if fact.get("importance") == "low":
                continue
            try:
                mem_result = _remember_fn(
                    text=fact["text"],
                    collection="memory_personal",
                    subject=fact.get("subject", ""),
                    tags=fact.get("tags", []),
                    source="conversation_hook",
                    summarize=False,
                )
                stored.append({"text": fact["text"], "id": mem_result.get("id")})
            except Exception:
                pass
        results["stored_facts"] = stored

    # Store conversation summary
    if extraction.get("conversation_summary"):
        try:
            _remember_fn(
                text=extraction["conversation_summary"],
                collection="conversation_summaries",
                subject=body.session_id or "session",
                tags=["conversation_summary"],
                source="conversation_hook",
                summarize=False,
            )
            results["summary_stored"] = True
        except Exception:
            results["summary_stored"] = False

    # Apply profile updates from the same merged response (no extra LLM call)
    profile_updates = data.get("profile_updates", {})
    if profile_updates:
        try:
            update_profile(profile_updates)
            results["profile_updated"] = True
            results["profile_changes"] = profile_updates
        except Exception as e:
            results["profile_updated"] = False
            results["profile_error"] = str(e)
    else:
        results["profile_updated"] = False

    return results

# --------------------------------------------------------------------------
# Context prefetch
# --------------------------------------------------------------------------
class PrefetchIn(BaseModel):
    query: str

@router.post("/context-prefetch")
def context_prefetch(body: PrefetchIn, request: Request):
    if _verify_token: _verify_token(request)
    from conversation_memory import build_context_prefetch
    from user_profile import load_profile

    def simple_search(query, collections, limit=5):
        """Adapter for the prefetch search."""
        results = _search_fn(query=query, collections=collections, limit=limit)
        return results.get("results", [])

    profile = load_profile()
    context = build_context_prefetch(body.query, simple_search, profile)
    return {"context": context, "profile": profile}

# --------------------------------------------------------------------------
# Conversation summarization
# --------------------------------------------------------------------------
class SummarizeIn(BaseModel):
    messages: list[dict[str, str]]
    session_id: str = ""
    store: bool = True

@router.post("/summarize-conversation")
def summarize_conversation_endpoint(body: SummarizeIn, request: Request):
    if _verify_token: _verify_token(request)
    from conversation_memory import summarize_conversation
    result = summarize_conversation(body.messages, _run_chat_fn, body.session_id)
    if body.store and result.get("summary"):
        try:
            _remember_fn(
                text=result["summary"],
                collection="conversation_summaries",
                subject=body.session_id or "session",
                tags=["conversation_summary", "full_summary"],
                source="summarize_endpoint",
                summarize=False,
            )
            result["stored"] = True
        except Exception:
            result["stored"] = False
    return result

# --------------------------------------------------------------------------
# Memory compaction
# --------------------------------------------------------------------------
class CompactIn(BaseModel):
    subject: str
    collection: str = "memory_personal"
    limit: int = 20

@router.post("/compact")
def compact_endpoint(body: CompactIn, request: Request):
    return _compact_logic(body, request)

@router.post("/compact-memories")
def compact_memories_endpoint(body: CompactIn, request: Request):
    return _compact_logic(body, request)

def _compact_logic(body: CompactIn, request: Request):
    if _verify_token: _verify_token(request)
    from conversation_memory import compact_memories

    # Search for memories on this subject
    results = _search_fn(query=body.subject, collections=[body.collection], limit=body.limit)
    memories = results.get("results", [])
    if len(memories) < 2:
        return {"compacted": False, "reason": f"only {len(memories)} memories found"}

    compaction = compact_memories(body.subject, memories, _run_chat_fn)
    if compaction.get("compacted") and compaction.get("merged_text"):
        # Store the merged memory
        _remember_fn(
            text=compaction["merged_text"],
            collection=body.collection,
            subject=body.subject,
            tags=compaction.get("tags", []),
            source="compaction",
            summarize=False,
        )
        compaction["new_memory_stored"] = True
    return compaction

# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
class PlanIn(BaseModel):
    query: str
    context: str = ""
    execute: bool = False

@router.post("/plan")
def plan_endpoint(body: PlanIn, request: Request):
    if _verify_token: _verify_token(request)
    from planner import create_plan, execute_plan

    plan_result = create_plan(body.query, _run_chat_fn, body.context)
    if not body.execute or not plan_result.get("plan"):
        return plan_result

    def simple_search(q):
        return _search_fn(query=q, collections=[], limit=5)
    def simple_ask(q):
        results = _search_fn(query=q, collections=[], limit=5)
        snippets = [r.get("payload", {}).get("text", "")[:500] for r in results.get("results", [])]
        answer = _run_chat_fn("retrieval_answer", [
            {"role": "system", "content": "Answer from context."},
            {"role": "user", "content": json.dumps({"question": q, "context": snippets})},
        ], max_tokens=1200)
        return answer.get("text", "")
    def simple_remember(text):
        return _remember_fn(text=text, collection="memory_personal", source="planner", summarize=True)

    execution = execute_plan(
        plan_result["plan"], _run_chat_fn,
        search_fn=simple_search, ask_fn=simple_ask, remember_fn=simple_remember,
    )
    return {"plan": plan_result["plan"], "execution": execution}

# --------------------------------------------------------------------------
# Smart chat (classify → prefetch → route → respond)
# --------------------------------------------------------------------------
class SmartChatIn(BaseModel):
    messages: list[dict[str, str]]
    auto_classify: bool = True
    session_id: str = ""

@router.post("/smart-chat")
def smart_chat(body: SmartChatIn, request: Request):
    if _verify_token: _verify_token(request)
    from query_classifier import classify_query
    from conversation_memory import build_context_prefetch
    from user_profile import load_profile

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages required")

    last_user = ""
    for msg in reversed(body.messages):
        if msg.get("role") == "user":
            last_user = msg.get("content", "")
            break

    meta: dict[str, Any] = {}

    # 1. Classify
    classification = {"task_type": "fallback_general", "needs_retrieval": False, "classifier_used": False}
    if body.auto_classify and last_user:
        classification = classify_query(last_user, _run_chat_fn)
    meta["classification"] = classification

    # 2. Context prefetch with compression
    def simple_search(query, collections, limit=5):
        return _search_fn(query=query, collections=collections, limit=limit).get("results", [])

    profile = load_profile()
    context_block = ""

    augmented = list(body.messages)
    try:
        from context_compression import (
            needs_summarization, split_conversation, build_summarize_messages,
            inject_summary, assemble_context, deduplicate_by_embedding,
            get_cached_summary, set_cached_summary,
        )
        from token_optimizer import estimate_messages_tokens, compute_context_budget, resolve_model_for_task

        # Conversation compression with session cache
        if needs_summarization(augmented):
            cached_summ = get_cached_summary(body.session_id) if body.session_id else None
            if cached_summ:
                _, recent = split_conversation(augmented)
                augmented = inject_summary(cached_summ, recent)
                meta["conversation_compressed"] = True
                meta["summary_cached"] = True
            else:
                old_msgs, recent = split_conversation(augmented)
                if old_msgs:
                    try:
                        summ_req = build_summarize_messages(old_msgs)
                        summ_result = _run_chat_fn("conversation_summary", summ_req, max_tokens=600)
                        summary_text = summ_result.get("text", "")
                        augmented = inject_summary(summary_text, recent)
                        meta["conversation_compressed"] = True
                        if body.session_id and summary_text:
                            set_cached_summary(body.session_id, summary_text)
                    except Exception:
                        pass

        # Resolve the actual model name for budget calculation (not task_type)
        task_type = classification.get("task_type", "fallback_general")
        model_name = resolve_model_for_task(task_type)
        conv_tokens = estimate_messages_tokens(augmented)
        token_budget = compute_context_budget(model_name, conv_tokens)

        if classification.get("needs_retrieval") and last_user:
            results = simple_search(last_user, [], limit=6)
            # Deduplicate with embed_fn for semantic dedup
            results = deduplicate_by_embedding(results, [], augmented, embed_fn=_embed_fn_adapter)
            from user_profile import format_profile_block
            profile_block = format_profile_block(profile)
            context_block = assemble_context(profile_block, results, token_budget, augmented)
        elif profile:
            from user_profile import format_profile_block
            context_block = format_profile_block(profile)
    except ImportError:
        task_type = classification.get("task_type", "fallback_general")
        if classification.get("needs_retrieval") and last_user:
            context_block = build_context_prefetch(last_user, simple_search, profile)

    # 3. Build augmented messages with context
    if context_block:
        if augmented and augmented[0].get("role") == "system":
            augmented[0] = {"role": "system", "content": context_block + "\n\n" + augmented[0]["content"]}
        else:
            augmented.insert(0, {"role": "system", "content": context_block})
    meta["context_injected"] = bool(context_block)

    # 4. Route to the right model
    task_type = classification.get("task_type", "fallback_general")
    result = _run_chat_fn(task_type, augmented, max_tokens=2400)
    meta["task_type_used"] = task_type

    return {
        "text": result.get("text", ""),
        "attempts": result.get("attempts", []),
        "profile": result.get("profile", ""),
        "model": result.get("model", ""),
        "meta": meta,
    }

# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    chunk_id: str
    collection: str
    query: str
    signal: str  # "positive" or "negative"

@router.post("/feedback")
def feedback_endpoint(body: FeedbackIn, request: Request):
    if _verify_token: _verify_token(request)
    from feedback import record_feedback
    return record_feedback(body.chunk_id, body.collection, body.query, body.signal)

@router.get("/feedback-stats")
def feedback_stats_endpoint(request: Request):
    if _verify_token: _verify_token(request)
    from feedback import feedback_stats
    return feedback_stats()

# --------------------------------------------------------------------------
# User profile
# --------------------------------------------------------------------------
@router.get("/profile")
def get_profile(request: Request):
    if _verify_token: _verify_token(request)
    from user_profile import load_profile
    return load_profile()

class ProfileUpdateIn(BaseModel):
    name: str | None = None
    language: str | None = None
    style: str | None = None
    expertise: list[str] | None = None
    context: str | None = None
    preferences: dict[str, Any] | None = None

@router.post("/profile")
def update_profile_endpoint(body: ProfileUpdateIn, request: Request):
    if _verify_token: _verify_token(request)
    from user_profile import update_profile
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return update_profile(updates)

# --------------------------------------------------------------------------
# Tools: shell, web-fetch, notify
# --------------------------------------------------------------------------
class ShellIn(BaseModel):
    command: str

@router.post("/shell")
def shell_endpoint(body: ShellIn, request: Request):
    if _verify_token: _verify_token(request)
    from tools import run_shell_command
    return run_shell_command(body.command)

class WebFetchIn(BaseModel):
    url: str

@router.post("/web-fetch")
async def web_fetch_endpoint(body: WebFetchIn, request: Request):
    if _verify_token: _verify_token(request)
    from tools import web_fetch
    return await web_fetch(body.url)

class NotifyIn(BaseModel):
    message: str
    title: str = "nanobot"
    level: str = "info"

@router.post("/notify")
async def notify_endpoint(body: NotifyIn, request: Request):
    if _verify_token: _verify_token(request)
    from tools import send_notification
    return await send_notification(body.message, body.title, body.level)

# --------------------------------------------------------------------------
# Execute step (from planner)
# --------------------------------------------------------------------------
class ExecuteStepIn(BaseModel):
    tool: str
    input: dict[str, Any] | None = None

@router.post("/execute-step")
def execute_step_endpoint(body: ExecuteStepIn, request: Request):
    if _verify_token: _verify_token(request)
    from planner import execute_step

    step = {"action": body.tool, "description": body.tool, "input": json.dumps(body.input or {}), "depends_on": []}

    def simple_search(q):
        return _search_fn(query=q, collections=[], limit=5)
    def simple_ask(q):
        results = _search_fn(query=q, collections=[], limit=5)
        snippets = [r.get("payload", {}).get("text", "")[:500] for r in results.get("results", [])]
        answer = _run_chat_fn("retrieval_answer", [
            {"role": "system", "content": "Answer from context."},
            {"role": "user", "content": json.dumps({"question": q, "context": snippets})},
        ], max_tokens=1200)
        return answer.get("text", "")
    def simple_remember(text):
        return _remember_fn(text=text, collection="memory_personal", source="planner", summarize=True)

    return execute_step(step, {}, _run_chat_fn,
                        search_fn=simple_search, ask_fn=simple_ask, remember_fn=simple_remember)

# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    from dashboard import get_dashboard_html, DASHBOARD_ENABLED
    if not DASHBOARD_ENABLED:
        raise HTTPException(status_code=404, detail="dashboard disabled")
    return get_dashboard_html()
