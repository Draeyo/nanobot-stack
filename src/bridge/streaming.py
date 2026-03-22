"""SSE streaming for smart-chat with progress events."""
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

    # Phase 1: classify
    classification = {"task_type": "fallback_general", "needs_retrieval": False}
    if body.auto_classify and last_user_msg:
        yield _sse_event("progress", {"phase": "classify", "status": "started"})
        try:
            classification = classify_query(last_user_msg, _deps["run_chat_task"])
        except Exception:
            pass
        yield _sse_event("progress", {"phase": "classify", "status": "done", "result": classification})

    # Phase 2: compress conversation if long (with session cache)
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

    # Phase 3: profile
    system_parts = []
    profile_block = ""
    if body.inject_profile:
        yield _sse_event("progress", {"phase": "profile", "status": "started"})
        profile = load_profile()
        profile_block = format_profile_block(profile)
        if profile_block:
            system_parts.append(profile_block)
        yield _sse_event("progress", {"phase": "profile", "status": "done"})

    # Phase 4: retrieve with dedup (using embed_fn)
    retrieval_count = 0
    if body.auto_retrieve and classification.get("needs_retrieval") and last_user_msg:
        yield _sse_event("progress", {"phase": "retrieve", "status": "started"})
        try:
            results = _deps["search_fn"](query=last_user_msg, collections=[], limit=6).get("results", [])
            embed_fn = _deps.get("embed_fn")
            results = deduplicate_by_embedding(results, [], augmented, embed_fn=embed_fn)

            task_type = classification.get("task_type", "fallback_general")
            model_name = resolve_model_for_task(task_type)
            conv_tokens = estimate_messages_tokens(augmented)
            budget = compute_context_budget(model_name, conv_tokens)

            context = assemble_context(profile_block, results, budget, augmented)
            if context:
                system_parts = [context]
            retrieval_count = len(results)
        except Exception as e:
            yield _sse_event("progress", {"phase": "retrieve", "status": "error", "error": str(e)})
        yield _sse_event("progress", {"phase": "retrieve", "status": "done", "results_count": retrieval_count})

    # Phase 5: generate
    router_task = classification.get("task_type", "fallback_general")
    yield _sse_event("progress", {"phase": "generate", "status": "started", "router_task": router_task})

    if system_parts:
        context_block = "\n\n".join(system_parts)
        if augmented and augmented[0].get("role") == "system":
            augmented[0] = {"role": "system", "content": context_block + "\n\n" + augmented[0]["content"]}
        else:
            augmented.insert(0, {"role": "system", "content": context_block})

    try:
        answer = _deps["run_chat_task"](router_task, augmented, max_tokens=2400)
        elapsed = round(time.monotonic() - t0, 2)
        yield _sse_event("answer", {
            "text": answer["text"],
            "classification": classification,
            "router_task": router_task,
            "attempts": answer["attempts"],
            "compressed": compressed,
            "elapsed_seconds": elapsed,
        })
    except Exception as e:
        yield _sse_event("error", {"error": str(e)})

    yield _sse_event("done", {"elapsed_seconds": round(time.monotonic() - t0, 2)})


@router.post("/smart-chat-stream")
async def smart_chat_stream(body: SmartChatStreamIn, _=Depends(lambda: _deps["verify_token"])):
    """Streaming smart-chat via Server-Sent Events."""
    return StreamingResponse(
        _stream_smart_chat(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
