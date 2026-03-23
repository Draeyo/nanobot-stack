"""v10 extension endpoints — mounted onto the main FastAPI app.

Adds: /classify, /conversation-hook, /context-prefetch, /summarize-conversation,
/compact, /plan, /execute-plan, /smart-chat, /feedback, /feedback-stats,
/profile, /dashboard, /knowledge-graph, /explain, /export, /pii-check,
/code-execute, /plugins, /query-rewrite, /working-memory,
/workflows, /agent/status, /agent/history, /agent/run
"""
from __future__ import annotations
import json, logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
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
# Query rewriting (HyDE + multi-query)
# --------------------------------------------------------------------------
class QueryRewriteIn(BaseModel):
    query: str
    mode: str = "hyde"  # 'hyde', 'multi', 'both'

@router.post("/query-rewrite")
def query_rewrite_endpoint(body: QueryRewriteIn, request: Request):
    if _verify_token: _verify_token(request)
    from query_rewriter import rewrite_query
    return rewrite_query(body.query, _run_chat_fn, embed_fn=_embed_fn_adapter, mode=body.mode)

# --------------------------------------------------------------------------
# Conversation hook (post-conversation fact extraction + profile + KG)
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

    # Single merged LLM call: extract facts + profile updates together
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

    # Auto-remember extracted facts with memory type tagging
    if body.auto_remember and extraction.get("facts"):
        stored = []
        for fact in extraction["facts"]:
            if fact.get("importance") == "low":
                continue
            try:
                tags = fact.get("tags", [])
                # Tag episodic vs semantic memory
                if any(kw in fact.get("text", "").lower() for kw in ("today", "yesterday", "just", "meeting", "decided")):
                    tags = list(set(tags + ["episodic"]))
                else:
                    tags = list(set(tags + ["semantic"]))
                mem_result = _remember_fn(
                    text=fact["text"],
                    collection="memory_personal",
                    subject=fact.get("subject", ""),
                    tags=tags,
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

    # Apply profile updates
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

    # Knowledge graph extraction (async, non-blocking)
    try:
        from knowledge_graph import extract_and_store, KG_ENABLED
        if KG_ENABLED:
            all_facts_text = " ".join(f["text"] for f in extraction.get("facts", []))
            if all_facts_text:
                kg_result = extract_and_store(all_facts_text, _run_chat_fn)
                results["knowledge_graph"] = kg_result
    except Exception as e:
        results["knowledge_graph"] = {"error": str(e)}

    return results

# --------------------------------------------------------------------------
# Context prefetch
# --------------------------------------------------------------------------
class PrefetchIn(BaseModel):
    query: str

@router.post("/context-prefetch")
def context_prefetch(body: PrefetchIn, request: Request):
    if _verify_token: _verify_token(request)
    try:
        from conversation_memory import build_context_prefetch
        from user_profile import load_profile
    except ImportError:
        raise HTTPException(status_code=501, detail="Context prefetch modules not available")

    def simple_search(query, collections, limit=5):
        """
        Lightweight adapter around the main search function that only exposes
        sanitized result items to context prefetch, avoiding any internal
        diagnostic fields (for example, error messages from embedding attempts).
        """
        search_response = _search_fn(query=query, collections=collections, limit=limit)
        # Only propagate the actual search hits; ignore any extra metadata.
        return search_response.get("results", []) if isinstance(search_response, dict) else []

    try:
        profile = load_profile()
    except Exception:
        logger.exception("Failed to load user profile")
        profile = {}
    try:
        context = build_context_prefetch(body.query, simple_search, profile)
    except Exception:
        logger.exception("context prefetch failed")
        raise HTTPException(status_code=500, detail="Context prefetch failed")

    # Proactive hints from knowledge graph
    kg_context = ""
    try:
        from knowledge_graph import query_entity, KG_ENABLED
        if KG_ENABLED:
            # Extract key nouns and look them up in KG
            import re
            words = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", body.query)
            for word in words[:3]:
                kg = query_entity(word)
                if kg.get("found"):
                    rels = kg.get("outgoing_relations", [])[:3]
                    if rels:
                        kg_lines = [f"- {word} → {r['relation']} → {r['target']}" for r in rels]
                        kg_context += "\n## Knowledge graph\n" + "\n".join(kg_lines) + "\n"
                    break
    except Exception:
        pass

    return {"context": context + kg_context, "profile": profile}

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
    try:
        from conversation_memory import compact_memories
    except ImportError:
        raise HTTPException(status_code=501, detail="Compaction module not available")

    try:
        results = _search_fn(query=body.subject, collections=[body.collection], limit=body.limit)
        memories = results.get("results", [])
        if len(memories) < 2:
            return {"compacted": False, "reason": f"only {len(memories)} memories found"}

        compaction = compact_memories(body.subject, memories, _run_chat_fn)
        if compaction.get("compacted") and compaction.get("merged_text"):
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
    except HTTPException:
        raise
    except Exception:
        logger.exception("Memory compaction failed")
        raise HTTPException(status_code=500, detail="Memory compaction failed")

# --------------------------------------------------------------------------
# Tool adapters — reusable wrappers for planner and agent endpoints
# --------------------------------------------------------------------------
def _build_tool_registry() -> dict:
    """Build a dict of tool adapter functions for planner / orchestrator use."""
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

    def simple_shell(cmd):
        from tools import run_shell_command
        return run_shell_command(cmd)

    def simple_web_fetch(url):
        import asyncio
        from tools import web_fetch
        return asyncio.run(web_fetch(url))

    def simple_notify(msg):
        import asyncio
        from tools import send_notification
        return asyncio.run(send_notification(msg))

    return {
        "search_fn": simple_search, "ask_fn": simple_ask, "remember_fn": simple_remember,
        "shell_fn": simple_shell, "web_fn": simple_web_fetch, "notify_fn": simple_notify,
        # Agent-compatible names
        "run_command": simple_shell, "web_fetch": simple_web_fetch,
        "notify": simple_notify, "search_memory": simple_search,
    }


# --------------------------------------------------------------------------
# Planning (with parallel execution support)
# --------------------------------------------------------------------------
class PlanIn(BaseModel):
    query: str
    context: str = ""
    execute: bool = False
    parallel: bool = True

@router.post("/plan")
def plan_endpoint(body: PlanIn, request: Request):
    if _verify_token: _verify_token(request)
    from planner import create_plan, execute_plan, execute_plan_parallel

    plan_result = create_plan(body.query, _run_chat_fn, body.context)
    if not body.execute or not plan_result.get("plan"):
        return plan_result

    tool_fns = _build_tool_registry()

    if body.parallel:
        execution = execute_plan_parallel(plan_result["plan"], _run_chat_fn, **tool_fns)
    else:
        execution = execute_plan(plan_result["plan"], _run_chat_fn, **tool_fns)

    return {"plan": plan_result["plan"], "execution": execution}

# --------------------------------------------------------------------------
# Smart chat v2 (classify → HyDE → retrieve → sentiment → cite → self-critique)
# --------------------------------------------------------------------------
class SmartChatIn(BaseModel):
    messages: list[dict[str, str]]
    auto_classify: bool = True
    session_id: str = ""
    enable_hyde: bool = True
    enable_citations: bool = True
    enable_self_critique: bool = True

SELF_CRITIQUE_PROMPT = """Review your answer for accuracy and completeness.
If the answer has factual errors, missing important context, or could be significantly improved, rewrite it.
If the answer is already good, return it unchanged.
Only return the final answer text, nothing else."""

CITATION_INSTRUCTION = """When using information from the provided context, add inline citations
using [1], [2], etc. At the end, list all sources used. Format:
Sources:
[1] source_name — relevant quote or summary"""

@router.post("/smart-chat")
def smart_chat(body: SmartChatIn, request: Request = None):
    if request and _verify_token: _verify_token(request)
    try:
        return _smart_chat_inner(body)
    except HTTPException:
        raise
    except Exception:
        logger.exception("smart-chat pipeline failed")
        raise HTTPException(status_code=500, detail="Chat pipeline failed")


def _smart_chat_inner(body: SmartChatIn) -> dict[str, Any]:
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

    # 0. Working memory — check for cached context
    session = None
    try:
        from working_memory import working_memory
        session = working_memory.get_session(body.session_id)
        session.track_query(last_user)
    except Exception:
        pass

    # 1. Sentiment detection
    try:
        from sentiment import detect_session_tone
        tone_info = detect_session_tone(body.messages)
        meta["tone"] = tone_info
    except Exception:
        tone_info = {"tone": "neutral", "urgency": 0.3, "style_hint": ""}

    # 2. Classify
    classification = {"task_type": "fallback_general", "needs_retrieval": False, "classifier_used": False}
    if body.auto_classify and last_user:
        classification = classify_query(last_user, _run_chat_fn)
    meta["classification"] = classification

    # 3. Query rewriting (HyDE)
    hyde_vector = None
    if body.enable_hyde and classification.get("needs_retrieval") and last_user:
        try:
            from query_rewriter import rewrite_query
            rewrite_result = rewrite_query(last_user, _run_chat_fn, embed_fn=_embed_fn_adapter, mode="hyde")
            hyde_vector = rewrite_result.get("hyde_vector")
            meta["hyde_used"] = hyde_vector is not None
            if rewrite_result.get("hyde_passage"):
                meta["hyde_passage_preview"] = rewrite_result["hyde_passage"][:200]
        except Exception:
            meta["hyde_used"] = False

    # 4. Context prefetch with compression
    def simple_search(query, collections, limit=5):
        return _search_fn(query=query, collections=collections, limit=limit).get("results", [])

    profile = load_profile()
    context_block = ""
    retrieval_sources = []  # For citations

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

        task_type = classification.get("task_type", "fallback_general")
        model_name = resolve_model_for_task(task_type)
        conv_tokens = estimate_messages_tokens(augmented)
        token_budget = compute_context_budget(model_name, conv_tokens)

        if classification.get("needs_retrieval") and last_user:
            results = simple_search(last_user, [], limit=6)

            # Track retrieval in working memory — filter already-seen chunks
            if session:
                results = [r for r in results if not session.is_chunk_seen(str(r.get("id", "")))]
                session.track_retrieval([str(r.get("id", "")) for r in results])

            results = deduplicate_by_embedding(results, [], augmented, embed_fn=_embed_fn_adapter)

            # Build citations map
            for idx, r in enumerate(results):
                payload = r.get("payload", {})
                source_name = payload.get("title") or payload.get("source_name") or payload.get("path", "")
                retrieval_sources.append({
                    "index": idx + 1,
                    "source": source_name,
                    "text_preview": payload.get("text", "")[:200],
                    "score": round(r.get("final_score", r.get("score", 0)), 3),
                })

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

    # 5. Build augmented messages with context + tone + citation instructions
    system_additions = []
    if context_block:
        system_additions.append(context_block)

    # Add tone adaptation hint
    tone_hint = tone_info.get("style_hint", "")
    if tone_hint:
        system_additions.append(f"\n## Tone adaptation\n{tone_hint}")

    # Add citation instructions if retrieval was used
    if body.enable_citations and retrieval_sources:
        system_additions.append(f"\n{CITATION_INSTRUCTION}")

    # Proactive context from knowledge graph
    try:
        from knowledge_graph import query_entity, KG_ENABLED
        if KG_ENABLED and last_user:
            import re
            proper_nouns = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", last_user)
            kg_lines = []
            for noun in proper_nouns[:2]:
                kg = query_entity(noun)
                if kg.get("found"):
                    for r in kg.get("outgoing_relations", [])[:2]:
                        kg_lines.append(f"- {noun} → {r['relation']} → {r['target']}")
            if kg_lines:
                system_additions.append("## Known relationships\n" + "\n".join(kg_lines))
                meta["kg_context_injected"] = True
    except Exception:
        pass

    if system_additions:
        full_system = "\n\n".join(system_additions)
        if augmented and augmented[0].get("role") == "system":
            augmented[0] = {"role": "system", "content": full_system + "\n\n" + augmented[0]["content"]}
        else:
            augmented.insert(0, {"role": "system", "content": full_system})
    meta["context_injected"] = bool(system_additions)

    # 6. Route to the right model
    task_type = classification.get("task_type", "fallback_general")
    result = _run_chat_fn(task_type, augmented, max_tokens=2400)
    answer_text = result.get("text", "")
    meta["task_type_used"] = task_type

    # 7. Self-critique (reflection loop)
    if body.enable_self_critique and answer_text and len(answer_text) > 100:
        try:
            critique_result = _run_chat_fn("critique_review", [
                {"role": "system", "content": SELF_CRITIQUE_PROMPT},
                {"role": "user", "content": f"Original question: {last_user}\n\nAnswer to review:\n{answer_text}"},
            ], max_tokens=2400)
            revised = critique_result.get("text", "").strip()
            if revised and revised != answer_text and len(revised) > 50:
                meta["self_critique_applied"] = True
                answer_text = revised
            else:
                meta["self_critique_applied"] = False
        except Exception:
            meta["self_critique_applied"] = False

    # 8. Store in working memory
    if session:
        session.put(f"last_answer_{last_user[:50]}", answer_text[:500])
        topic = classification.get("raw_category", "")
        if topic:
            session.track_topic(topic)

    response = {
        "text": answer_text,
        "attempts": result.get("attempts", []),
        "profile": result.get("profile", ""),
        "model": result.get("model", ""),
        "meta": meta,
    }

    if body.enable_citations and retrieval_sources:
        response["sources"] = retrieval_sources

    return response


def smart_chat_pipeline(messages: list[dict[str, str]], session_id: str = "",
                         auto_classify: bool = True, enable_hyde: bool = True,
                         enable_citations: bool = False,
                         enable_self_critique: bool = True) -> dict[str, Any]:
    """Callable entry point for the smart-chat pipeline (used by channel adapters)."""
    body = SmartChatIn(
        messages=messages, auto_classify=auto_classify, session_id=session_id,
        enable_hyde=enable_hyde, enable_citations=enable_citations,
        enable_self_critique=enable_self_critique,
    )
    # Build a minimal mock request for the auth-free path
    return _smart_chat_inner(body)

# --------------------------------------------------------------------------
# Explain mode — show full pipeline trace
# --------------------------------------------------------------------------
class ExplainIn(BaseModel):
    query: str
    messages: list[dict[str, str]] = []

@router.post("/explain")
def explain_endpoint(body: ExplainIn, request: Request):
    if _verify_token: _verify_token(request)
    from query_classifier import classify_query

    trace: dict[str, Any] = {"query": body.query}

    # 1. Classification
    classification = classify_query(body.query, _run_chat_fn)
    trace["classification"] = classification

    # 2. Route preview
    task_type = classification.get("task_type", "fallback_general")
    from token_optimizer import resolve_model_for_task
    model = resolve_model_for_task(task_type)
    trace["routing"] = {"task_type": task_type, "model": model}

    # 3. Search preview
    results = _search_fn(query=body.query, collections=[], limit=5)
    trace["search_results"] = [
        {
            "source": r.get("payload", {}).get("title") or r.get("payload", {}).get("path", ""),
            "score": round(r.get("final_score", r.get("score", 0)), 3),
            "preview": r.get("payload", {}).get("text", "")[:200],
            "reranker": r.get("reranker", "unknown"),
        }
        for r in results.get("results", [])
    ]

    # 4. Tone detection
    try:
        from sentiment import detect_tone
        trace["tone"] = detect_tone(body.query)
    except Exception:
        trace["tone"] = {"tone": "neutral"}

    # 5. Knowledge graph check
    try:
        from knowledge_graph import query_entity, KG_ENABLED
        if KG_ENABLED:
            import re
            nouns = re.findall(r"\b[A-Z][a-z]+\b", body.query)
            kg_results = {}
            for noun in nouns[:3]:
                kg = query_entity(noun)
                if kg.get("found"):
                    kg_results[noun] = {
                        "type": kg["entity"]["type"],
                        "relations": len(kg.get("outgoing_relations", []) + kg.get("incoming_relations", [])),
                    }
            trace["knowledge_graph"] = kg_results
    except Exception:
        pass

    return trace

# --------------------------------------------------------------------------
# Knowledge graph endpoints
# --------------------------------------------------------------------------
class KGQueryIn(BaseModel):
    entity: str
    depth: int = 1

class KGRelationIn(BaseModel):
    entity1: str
    entity2: str

@router.post("/knowledge-graph/query")
def kg_query(body: KGQueryIn, request: Request):
    if _verify_token: _verify_token(request)
    from knowledge_graph import query_entity
    return query_entity(body.entity, body.depth)

@router.post("/knowledge-graph/relations")
def kg_relations(body: KGRelationIn, request: Request):
    if _verify_token: _verify_token(request)
    from knowledge_graph import query_relations
    return query_relations(body.entity1, body.entity2)

@router.get("/knowledge-graph/stats")
def kg_stats(request: Request):
    if _verify_token: _verify_token(request)
    try:
        from knowledge_graph import get_stats
        return get_stats()
    except Exception:
        logger.exception("Failed to get KG stats")
        raise HTTPException(status_code=500, detail="Knowledge graph stats unavailable")

# --------------------------------------------------------------------------
# Code interpreter
# --------------------------------------------------------------------------
class CodeExecIn(BaseModel):
    code: str
    timeout: int | None = None

@router.post("/code-execute")
def code_execute_endpoint(body: CodeExecIn, request: Request):
    if _verify_token: _verify_token(request)
    from code_interpreter import execute_code
    return execute_code(body.code, body.timeout)

# --------------------------------------------------------------------------
# PII checking
# --------------------------------------------------------------------------
class PIICheckIn(BaseModel):
    text: str

@router.post("/pii-check")
def pii_check_endpoint(body: PIICheckIn, request: Request):
    if _verify_token: _verify_token(request)
    from pii_filter import check_text
    return check_text(body.text)

# --------------------------------------------------------------------------
# Conversation export
# --------------------------------------------------------------------------
class ExportIn(BaseModel):
    messages: list[dict[str, str]]
    format: str = "markdown"  # 'markdown', 'json', 'pdf'
    title: str = "Conversation Export"
    session_id: str = ""
    summary: str = ""

@router.post("/export")
def export_endpoint(body: ExportIn, request: Request):
    if _verify_token: _verify_token(request)
    from export import export_markdown, export_structured, generate_pdf_bytes

    if body.format == "json":
        return export_structured(body.messages, body.session_id, body.summary)
    elif body.format == "pdf":
        md = export_markdown(body.messages, body.title, body.session_id)
        pdf_bytes = generate_pdf_bytes(md, body.title)
        if pdf_bytes:
            return Response(content=pdf_bytes, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{body.title}.pdf"'})
        return {"error": "PDF generation unavailable (install reportlab)"}
    else:
        md = export_markdown(body.messages, body.title, body.session_id)
        return {"markdown": md}

# --------------------------------------------------------------------------
# Plugin management
# --------------------------------------------------------------------------
@router.get("/plugins")
def list_plugins(request: Request):
    if _verify_token: _verify_token(request)
    try:
        from plugins import plugin_registry
        return {"plugins": plugin_registry.list_plugins(), "tools": plugin_registry.list_tools()}
    except Exception:
        logger.exception("Failed to list plugins")
        return {"plugins": [], "tools": [], "error": "Failed to list plugins"}

class PluginToolIn(BaseModel):
    tool_name: str
    params: dict[str, Any] = {}

@router.post("/plugin-tool")
def run_plugin_tool(body: PluginToolIn, request: Request):
    if _verify_token: _verify_token(request)
    from plugins import plugin_registry
    return plugin_registry.run_tool(body.tool_name, **body.params)

# --------------------------------------------------------------------------
# Working memory status
# --------------------------------------------------------------------------
@router.get("/working-memory")
def working_memory_status(request: Request):
    if _verify_token: _verify_token(request)
    from working_memory import working_memory
    return working_memory.stats()

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
    try:
        from tools import web_fetch
        result = await web_fetch(body.url)
    except Exception:
        logger.error("web_fetch failed")
        raise HTTPException(status_code=502, detail="Web fetch failed")
    return result

class NotifyIn(BaseModel):
    message: str
    title: str = "nanobot"
    level: str = "info"

@router.post("/notify")
async def notify_endpoint(body: NotifyIn, request: Request):
    if _verify_token: _verify_token(request)
    try:
        from tools import send_notification
        result = await send_notification(body.message, body.title, body.level)
    except Exception:
        logger.error("send_notification failed")
        raise HTTPException(status_code=502, detail="Notification delivery failed")
    return result

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
    try:
        from dashboard import get_dashboard_html, DASHBOARD_ENABLED
    except ImportError:
        raise HTTPException(status_code=404, detail="Dashboard not available")
    if not DASHBOARD_ENABLED:
        raise HTTPException(status_code=404, detail="Dashboard disabled")
    return get_dashboard_html()

# --------------------------------------------------------------------------
# Channel adapter status
# --------------------------------------------------------------------------
@router.get("/channels/status")
def channel_status_endpoint(request: Request):
    if _verify_token: _verify_token(request)
    try:
        from channels import channel_manager
        return channel_manager.status()
    except Exception:
        logger.exception("Failed to get channel status")
        return {"error": "Failed to retrieve channel status", "channels": {}}


# --------------------------------------------------------------------------
# v10: Procedural Workflows
# --------------------------------------------------------------------------
@router.get("/workflows")
def list_workflows_endpoint(request: Request, limit: int = 50):
    """List learned procedural workflows."""
    if _verify_token: _verify_token(request)
    try:
        from procedural_memory import get_workflows, PROCEDURAL_MEMORY_ENABLED
        if not PROCEDURAL_MEMORY_ENABLED:
            return {"workflows": [], "enabled": False}
        return {"workflows": get_workflows(limit), "enabled": True}
    except Exception:
        logger.debug("Procedural memory not available")
        return {"workflows": [], "enabled": False}


class ToggleWorkflowIn(BaseModel):
    auto_suggest: bool


@router.post("/workflows/{workflow_id}/toggle")
def toggle_workflow_endpoint(workflow_id: int, body: ToggleWorkflowIn, request: Request):
    """Enable or disable auto-suggestion for a workflow."""
    if _verify_token: _verify_token(request)
    try:
        from procedural_memory import toggle_auto_suggest
        return toggle_auto_suggest(workflow_id, body.auto_suggest)
    except Exception:
        logger.exception("Failed to toggle workflow %s", workflow_id)
        return {"ok": False, "error": "procedural memory not available"}


# --------------------------------------------------------------------------
# v10: Agent Orchestrator
# --------------------------------------------------------------------------
_agent_history: list[dict] = []


@router.get("/agent/status")
def agent_status_endpoint(request: Request):
    """List registered agents and their capabilities."""
    if _verify_token: _verify_token(request)
    try:
        from agents import list_agents
        return {"agents": list_agents(), "orchestrator_enabled": _is_orchestrator_enabled()}
    except Exception:
        return {"agents": [], "orchestrator_enabled": False}


@router.get("/agent/history")
def agent_history_endpoint(request: Request, limit: int = 50):
    """Return recent agent execution history."""
    if _verify_token: _verify_token(request)
    return {"executions": _agent_history[-limit:]}


class AgentRunIn(BaseModel):
    task: str
    context: dict = {}


@router.post("/agent/run")
def agent_run_endpoint(body: AgentRunIn, request: Request):
    """Run a task through the orchestrator agent."""
    if _verify_token: _verify_token(request)
    if not _is_orchestrator_enabled():
        return {"ok": False, "error": "Agent orchestrator is disabled (AGENT_ORCHESTRATOR_ENABLED=false)"}

    try:
        import asyncio
        from agents import get_agent_class
        orch_cls = get_agent_class("orchestrator")
        if not orch_cls:
            return {"ok": False, "error": "Orchestrator agent not registered"}

        tool_registry = _build_tool_registry()
        agent = orch_cls(run_chat_fn=_run_chat_fn, tool_registry=tool_registry)
        result = asyncio.run(agent.run(body.task, body.context))

        # Record in history
        from datetime import datetime, timezone
        _agent_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "orchestrator",
            "task": body.task[:200],
            "status": result.status,
            "tokens": result.cost_tokens,
        })
        # Cap history
        if len(_agent_history) > 200:
            _agent_history[:] = _agent_history[-200:]

        return {
            "ok": True,
            "status": result.status,
            "output": result.output,
            "actions_taken": result.actions_taken,
            "cost_tokens": result.cost_tokens,
        }
    except Exception:
        logger.exception("Agent run failed")
        return {"ok": False, "error": "agent execution failed"}


def _is_orchestrator_enabled() -> bool:
    import os
    return os.getenv("AGENT_ORCHESTRATOR_ENABLED", "false").lower() == "true"
