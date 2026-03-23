"""Multi-step task planning and execution.

Decomposes complex queries into step-by-step plans and executes each step
using the appropriate tool/model. Supports parallel execution of independent steps.
"""
from __future__ import annotations
import concurrent.futures
import json, logging, os, uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.planner")
PLANNER_ENABLED = os.getenv("PLANNER_ENABLED", "true").lower() == "true"
MAX_PARALLEL_WORKERS = int(os.getenv("PLANNER_MAX_WORKERS", "4"))

# Procedural memory integration (set during app init)
_procedural_memory = None


def set_procedural_memory(pm_module) -> None:
    """Wire procedural memory for action logging."""
    global _procedural_memory
    _procedural_memory = pm_module

PLAN_PROMPT = """You are a task planner. Decompose the user's request into sequential steps.
Each step should be a concrete, actionable task.

Return ONLY JSON:
{
  "goal": "one-line description of the overall goal",
  "steps": [
    {"id": 1, "action": "search_memory|ask_rag|run_command|web_fetch|generate_text|notify|remember", "description": "what to do", "input": "parameters or query for this step", "depends_on": []},
    {"id": 2, "action": "generate_text", "description": "synthesise results", "input": "...", "depends_on": [1]}
  ],
  "estimated_steps": 3
}

Available actions:
- search_memory: search vector memory for relevant info
- ask_rag: ask a retrieval-grounded question
- generate_text: use an LLM to write/analyse/summarise
- run_command: execute a pre-approved shell command (read-only)
- web_fetch: fetch a URL and extract content
- notify: send a notification
- remember: store something in memory

Keep plans to 2-6 steps. Prefer fewer, broader steps over many tiny ones.
Mark steps as depends_on: [] if they are independent and can run in parallel."""


def create_plan(query: str, run_chat_fn, context: str = "") -> dict[str, Any]:
    if not PLANNER_ENABLED:
        return {"plan": None, "error": "planner disabled"}
    prompt = query
    if context:
        prompt = f"Context:\n{context}\n\nRequest:\n{query}"
    try:
        result = run_chat_fn("tool_planning", [
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user", "content": prompt[:4000]},
        ], json_mode=True, max_tokens=1000)
        plan = json.loads(result["text"])
        plan["plan_id"] = str(uuid.uuid4())[:8]
        plan["created_at"] = datetime.now(timezone.utc).isoformat()
        plan["status"] = "created"
        return {"plan": plan}
    except Exception as exc:
        logger.warning("Planning failed: %s", exc)
        return {"plan": None, "error": "planning failed"}


def execute_step(step: dict[str, Any], step_results: dict[int, Any],
                 run_chat_fn, search_fn=None, ask_fn=None,
                 shell_fn=None, web_fn=None, remember_fn=None, notify_fn=None) -> dict[str, Any]:
    """Execute a single plan step using the appropriate tool."""
    action = step.get("action", "generate_text")
    description = step.get("description", "")
    step_input = step.get("input", "")

    # Inject results from dependencies
    deps = step.get("depends_on", [])
    dep_context = ""
    for dep_id in deps:
        dep_result = step_results.get(dep_id)
        if dep_result:
            dep_context += f"\n[Result from step {dep_id}]: {str(dep_result)[:1500]}\n"

    full_input = f"{step_input}\n{dep_context}".strip() if dep_context else step_input

    try:
        if action == "search_memory" and search_fn:
            result = search_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        elif action == "ask_rag" and ask_fn:
            result = ask_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        elif action == "run_command" and shell_fn:
            result = shell_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        elif action == "web_fetch" and web_fn:
            result = web_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        elif action == "remember" and remember_fn:
            result = remember_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        elif action == "notify" and notify_fn:
            result = notify_fn(full_input)
            output = {"status": "ok", "action": action, "result": result}

        else:
            # Default: generate_text
            result = run_chat_fn("fallback_general", [
                {"role": "system", "content": f"Task: {description}"},
                {"role": "user", "content": full_input[:4000]},
            ], max_tokens=1800)
            output = {"status": "ok", "action": "generate_text", "result": result["text"]}

        # Log to procedural memory
        if _procedural_memory:
            try:
                _procedural_memory.log_action(
                    session_id="planner",
                    action=action,
                    params={"input": step_input[:200], "description": description},
                    result_summary=str(output.get("result", ""))[:500],
                )
            except Exception:
                pass

        return output

    except Exception as exc:
        logger.warning("Step execution failed (%s): %s", action, exc)
        result_dict = {"status": "error", "action": action, "error": "step execution failed"}
        # Log to procedural memory even on failure
        if _procedural_memory:
            try:
                _procedural_memory.log_action(
                    session_id="planner",
                    action=action,
                    params={"input": step_input[:200], "description": description},
                    result_summary=f"error: {exc}",
                )
            except Exception:
                pass
        return result_dict


def execute_plan(plan: dict[str, Any], run_chat_fn, **tool_fns) -> dict[str, Any]:
    """Execute all steps in a plan sequentially."""
    steps = plan.get("steps", [])
    results: dict[int, Any] = {}
    step_outputs = []

    for step in steps:
        step_id = step.get("id", 0)
        output = execute_step(step, results, run_chat_fn, **tool_fns)
        results[step_id] = output.get("result", output.get("error", ""))
        step_outputs.append({"step": step, "output": output})

        if output.get("status") == "error":
            logger.warning("Plan step %d failed, continuing...", step_id)

    return {
        "plan_id": plan.get("plan_id", ""),
        "goal": plan.get("goal", ""),
        "steps_executed": len(step_outputs),
        "results": step_outputs,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "sequential",
    }


def execute_plan_parallel(plan: dict[str, Any], run_chat_fn, **tool_fns) -> dict[str, Any]:
    """Execute plan steps with parallelism for independent steps.

    Steps with no dependencies (depends_on: []) or whose dependencies are
    already complete can run in parallel.
    """
    steps = plan.get("steps", [])
    results: dict[int, Any] = {}
    step_outputs: list[dict[str, Any]] = []
    completed: set[int] = set()

    # Build dependency graph
    steps_by_id = {s["id"]: s for s in steps}
    remaining = set(s["id"] for s in steps)

    while remaining:
        # Find steps whose dependencies are all met
        ready = []
        for sid in remaining:
            step = steps_by_id[sid]
            deps = set(step.get("depends_on", []))
            if deps.issubset(completed):
                ready.append(step)

        if not ready:
            # Deadlock — execute remaining sequentially
            for sid in list(remaining):
                step = steps_by_id[sid]
                output = execute_step(step, results, run_chat_fn, **tool_fns)
                results[sid] = output.get("result", output.get("error", ""))
                step_outputs.append({"step": step, "output": output})
                completed.add(sid)
                remaining.discard(sid)
            break

        if len(ready) == 1:
            # Only one step ready — run directly
            step = ready[0]
            output = execute_step(step, results, run_chat_fn, **tool_fns)
            results[step["id"]] = output.get("result", output.get("error", ""))
            step_outputs.append({"step": step, "output": output})
            completed.add(step["id"])
            remaining.discard(step["id"])
        else:
            # Multiple independent steps — run in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(ready), MAX_PARALLEL_WORKERS)) as pool:
                futures = {}
                for step in ready:
                    f = pool.submit(execute_step, step, results, run_chat_fn, **tool_fns)
                    futures[f] = step

                for future in concurrent.futures.as_completed(futures):
                    step = futures[future]
                    try:
                        output = future.result()
                    except Exception:
                        output = {"status": "error", "action": step.get("action"), "error": "parallel step failed"}
                    results[step["id"]] = output.get("result", output.get("error", ""))
                    step_outputs.append({"step": step, "output": output})
                    completed.add(step["id"])
                    remaining.discard(step["id"])

    return {
        "plan_id": plan.get("plan_id", ""),
        "goal": plan.get("goal", ""),
        "steps_executed": len(step_outputs),
        "results": step_outputs,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "parallel",
    }
