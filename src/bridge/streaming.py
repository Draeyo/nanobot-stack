"""SSE streaming for smart-chat v2 with progress events.

Mirrors the extensions.py /smart-chat pipeline but streams progress events
via Server-Sent Events: classify → sentiment → HyDE → compress → profile →
retrieve → generate → self-critique → done.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.streaming")
router = APIRouter()
_deps: dict[str, Any] = {}

def set_dependencies(deps: dict[str, Any]):
    _deps.update(deps)

class SmartChatStreamIn(BaseModel):
    messages: list[dict[str, str]]
    auto_classify: bool = True
    auto_retrieve: bool = True
    inject_profile: bool = True
    session_id: str = ""
    enable_hyde: bool = True
    enable_citations: bool = True
    enable_self_critique: bool = True

def _sse_event(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"

async def _stream_smart_chat(body: SmartChatStreamIn) -> AsyncGenerator[str, None]:
    from query_classifier import classify_query
    from user_profile import load_profile, format_profile_block
    from context_compression import (
        needs_summarization, split_conversation, build_summarize_messages,
        inject_summary, assemble_context, deduplicate_by_embedding,
        get_cached_summary, set_cached_summary,
    )
    from token_optimizer import estimate_messages_tokens, compute_context_budget, resolve_model_for_task

    t0 = time.monotonic()

    last_user_msg = ""
    for m in reversed(body.messages):
        if m.get("role") == "user":
            last_user_msg = m["content"]
            break

    # Phase 0: Working memory — track query
    session = None
    try:
        from working_memory import working_memory
        session = working_memory.get_session(body.session_id)
        session.track_query(last_user_msg)
    except Exception:
        pass

    # Phase 1: Sentiment detection
    tone_info = {"tone": "neutral", "urgency": 0.3, "style_hint": ""}
    yield _sse_event("progress", {"phase": "sentiment", "status": "started"})
    try:
        from sentiment import detect_session_tone
        tone_info = detect_session_tone(body.messages)
    except Exception:
        pass
    yield _sse_event("progress", {"phase": "sentiment", "status": "done", "result": tone_info})

    # Phase 2: Classify
    classification = {"task_type": "fallback_general", "needs_retrieval": False}
    if body.auto_classify and last_user_msg:
        yield _sse_event("progress", {"phase": "classify", "status": "started"})
        try:
            classification = classify_query(last_user_msg, _deps["run_chat_task"])
        except Exception:
            pass
        yield _sse_event("progress", {"phase": "classify", "status": "done", "result": classification})

    # Phase 3: HyDE query rewriting
    hyde_vector = None
    if body.enable_hyde and classification.get("needs_retrieval") and last_user_msg:
        yield _sse_event("progress", {"phase": "hyde", "status": "started"})
        try:
            from query_rewriter import rewrite_query
            rewrite_result = rewrite_query(
                last_user_msg, _deps["run_chat_task"],
                embed_fn=_deps.get("embed_fn"), mode="hyde",
            )
            hyde_vector = rewrite_result.get("hyde_vector")
            yield _sse_event("progress", {
                "phase": "hyde", "status": "done",
                "hyde_used": hyde_vector is not None,
                "passage_preview": (rewrite_result.get("hyde_passage") or "")[:200],
            })
        except Exception:
            yield _sse_event("progress", {"phase": "hyde", "status": "done", "hyde_used": False})

    # Phase 4: Compress conversation if long (with session cache)
    augmented = list(body.messages)
    compressed = False
    if needs_summarization(augmented):
        yield _sse_event("progress", {"phase": "compress", "status": "started"})
        cached_summ = get_cached_summary(body.session_id) if body.session_id else None
        if cached_summ:
            _, recent = split_conversation(augmented)
            augmented = inject_summary(cached_summ, recent)
            compressed = True
        else:
            try:
                old_msgs, recent = split_conversation(augmented)
                if old_msgs:
                    summ_req = build_summarize_messages(old_msgs)
                    summ_resp = _deps["run_chat_task"]("conversation_summary", summ_req, max_tokens=600)
                    summary_text = summ_resp.get("text", "")
                    augmented = inject_summary(summary_text, recent)
                    compressed = True
                    if body.session_id and summary_text:
                        set_cached_summary(body.session_id, summary_text)
            except Exception:
                pass
        yield _sse_event("progress", {"phase": "compress", "status": "done", "compressed": compressed})

    # Phase 5: Profile
    system_parts = []
    profile_block = ""
    if body.inject_profile:
        yield _sse_event("progress", {"phase": "profile", "status": "started"})
        profile = load_profile()
        profile_block = format_profile_block(profile)
        if profile_block:
            system_parts.append(profile_block)
        yield _sse_event("progress", {"phase": "profile", "status": "done"})

    # Phase 6: Retrieve with dedup
    retrieval_count = 0
    retrieval_sources = []
    if body.auto_retrieve and classification.get("needs_retrieval") and last_user_msg:
        yield _sse_event("progress", {"phase": "retrieve", "status": "started"})
        try:
            results = _deps["search_fn"](query=last_user_msg, collections=[], limit=6).get("results", [])
            embed_fn = _deps.get("embed_fn")

            # Filter already-seen chunks via working memory
            if session:
                results = [r for r in results if not session.is_chunk_seen(str(r.get("id", "")))]
                session.track_retrieval([str(r.get("id", "")) for r in results])

            results = deduplicate_by_embedding(results, [], augmented, embed_fn=embed_fn)

            task_type = classification.get("task_type", "fallback_general")
            model_name = resolve_model_for_task(task_type)
            conv_tokens = estimate_messages_tokens(augmented)
            budget = compute_context_budget(model_name, conv_tokens)

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

            context = assemble_context(profile_block, results, budget, augmented)
            if context:
                system_parts = [context]
            retrieval_count = len(results)
        except Exception as e:
            yield _sse_event("progress", {"phase": "retrieve", "status": "error", "error": str(e)})
        yield _sse_event("progress", {"phase": "retrieve", "status": "done", "results_count": retrieval_count})

    # Build system message with tone + citation instructions
    tone_hint = tone_info.get("style_hint", "")
    if tone_hint:
        system_parts.append(f"\n## Tone adaptation\n{tone_hint}")

    if body.enable_citations and retrieval_sources:
        system_parts.append(
            "\nWhen using information from the provided context, add inline citations "
            "using [1], [2], etc. At the end, list all sources used."
        )

    # Knowledge graph context
    try:
        from knowledge_graph import query_entity, KG_ENABLED
        if KG_ENABLED and last_user_msg:
            import re
            proper_nouns = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", last_user_msg)
            kg_lines = []
            for noun in proper_nouns[:2]:
                kg = query_entity(noun)
                if kg.get("found"):
                    for r in kg.get("outgoing_relations", [])[:2]:
                        kg_lines.append(f"- {noun} → {r['relation']} → {r['target']}")
            if kg_lines:
                system_parts.append("## Known relationships\n" + "\n".join(kg_lines))
    except Exception:
        pass

    if system_parts:
        context_block = "\n\n".join(system_parts)
        if augmented and augmented[0].get("role") == "system":
            augmented[0] = {"role": "system", "content": context_block + "\n\n" + augmented[0]["content"]}
        else:
            augmented.insert(0, {"role": "system", "content": context_block})

    # Phase 7: Generate
    router_task = classification.get("task_type", "fallback_general")
    yield _sse_event("progress", {"phase": "generate", "status": "started", "router_task": router_task})

    try:
        answer = _deps["run_chat_task"](router_task, augmented, max_tokens=2400)
        answer_text = answer["text"]

        # Phase 8: Self-critique
        if body.enable_self_critique and answer_text and len(answer_text) > 100:
            yield _sse_event("progress", {"phase": "self_critique", "status": "started"})
            try:
                critique_result = _deps["run_chat_task"]("critique_review", [
                    {"role": "system", "content": "Review your answer for accuracy and completeness. "
                     "If the answer has factual errors or could be significantly improved, rewrite it. "
                     "If it's good, return it unchanged. Only output the final answer."},
                    {"role": "user", "content": f"Original question: {last_user_msg}\n\nAnswer to review:\n{answer_text}"},
                ], max_tokens=2400)
                revised = critique_result.get("text", "").strip()
                critique_applied = bool(revised and revised != answer_text and len(revised) > 50)
                if critique_applied:
                    answer_text = revised
                yield _sse_event("progress", {"phase": "self_critique", "status": "done", "applied": critique_applied})
            except Exception:
                yield _sse_event("progress", {"phase": "self_critique", "status": "done", "applied": False})

        # Store in working memory
        if session:
            session.put(f"last_answer_{last_user_msg[:50]}", answer_text[:500])
            topic = classification.get("raw_category", "")
            if topic:
                session.track_topic(topic)

        elapsed = round(time.monotonic() - t0, 2)
        answer_event: dict[str, Any] = {
            "text": answer_text,
            "classification": classification,
            "router_task": router_task,
            "attempts": answer["attempts"],
            "compressed": compressed,
            "tone": tone_info,
            "elapsed_seconds": elapsed,
        }
        if body.enable_citations and retrieval_sources:
            answer_event["sources"] = retrieval_sources
        yield _sse_event("answer", answer_event)
    except Exception as e:
        yield _sse_event("error", {"error": str(e)})

    yield _sse_event("done", {"elapsed_seconds": round(time.monotonic() - t0, 2)})


@router.post("/smart-chat-stream")
async def smart_chat_stream(body: SmartChatStreamIn, _=Depends(lambda: _deps["verify_token"])):
    """Streaming smart-chat v2 via Server-Sent Events."""
    return StreamingResponse(
        _stream_smart_chat(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
