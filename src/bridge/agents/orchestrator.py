"""Orchestrator agent — hierarchical task decomposition and delegation.

Decomposes complex tasks into sub-tasks, assigns each to the appropriate
specialist agent (self or ops for v10), collects results, and synthesizes
a unified response.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from .base import AgentBase, AgentResult

logger = logging.getLogger("rag-bridge.agents.orchestrator")

ORCHESTRATOR_ENABLED = (
    os.getenv("AGENT_ORCHESTRATOR_ENABLED", "false").lower() == "true"
)

ORCHESTRATE_PROMPT = """\
You are a task orchestrator. Decompose the user's request into sub-tasks.
For each sub-task, assign to an agent:
- "self": handle directly (simple text generation, memory lookup, factual answers)
- "ops": server/infrastructure tasks (monitoring, diagnostics, maintenance, logs, status)

Return ONLY JSON:
{
  "goal": "one-line description",
  "subtasks": [
    {"id": 1, "agent": "self", "task": "what to do", "depends_on": [], "priority": "high|medium|low"}
  ],
  "estimated_cost": "low|medium|high"
}

Keep to 1-5 subtasks. If the request is simple and can be handled by one agent, \
return a single subtask.
Prefer fewer subtasks over many small ones."""


class OrchestratorAgent(AgentBase):
    """Decomposes complex tasks and delegates to specialist agents."""

    name: str = "orchestrator"
    description: str = "Decomposes complex tasks and delegates to specialist agents"
    tools: list[str] = [
        "search_memory",
        "ask_rag",
        "generate_text",
        "run_command",
        "web_fetch",
        "remember",
        "notify",
    ]
    max_steps: int = 15

    def __init__(
        self,
        run_chat_fn: Callable[..., Any],
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        trust_engine: Any = None,
        agent_registry: dict[str, AgentBase] | None = None,
    ) -> None:
        super().__init__(run_chat_fn, tool_registry, trust_engine)
        self.agent_registry: dict[str, AgentBase] = agent_registry or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AgentResult:
        """Decompose task, delegate, collect results, synthesize."""
        if not ORCHESTRATOR_ENABLED:
            return self._make_result("failed", "Orchestrator is disabled")

        # Step 1: Decompose
        decomposition = self._decompose(task, context)
        if not decomposition:
            return self._make_result("failed", "Failed to decompose task")

        # Step 2: Convert subtasks to planner format and execute
        plan = self._build_plan(decomposition)

        # Step 3: Execute via planner (import at call time to avoid circular)
        try:
            from planner import execute_plan_parallel  # type: ignore[import-untyped]

            result = execute_plan_parallel(
                plan, self.run_chat_fn, **self.tool_registry
            )
        except Exception as exc:
            logger.error("Plan execution failed: %s", exc)
            return self._make_result("failed", f"Execution failed: {exc}")

        # Step 4: Synthesize results
        goal = decomposition.get("goal", "")
        output = self._synthesize(goal, result)
        self._log_action(
            "orchestrate",
            {
                "goal": goal,
                "subtasks": len(decomposition.get("subtasks", [])),
            },
            output[:200],
        )

        return self._make_result("completed", output)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decompose(
        self, task: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Use LLM to decompose the task into subtasks."""
        context_str = ""
        if context:
            context_str = f"\nContext: {json.dumps(context)[:1000]}"
        try:
            result = self.run_chat_fn(
                "tool_planning",
                [
                    {"role": "system", "content": ORCHESTRATE_PROMPT},
                    {"role": "user", "content": f"{task}{context_str}"[:4000]},
                ],
                json_mode=True,
                max_tokens=1000,
            )
            return json.loads(result["text"])
        except Exception as exc:
            logger.warning("Orchestrator decomposition failed: %s", exc)
            return None

    def _subtask_to_plan_step(self, subtask: dict[str, Any]) -> dict[str, Any]:
        """Convert orchestrator subtask format to planner step format."""
        agent = subtask.get("agent", "self")
        task_text = subtask.get("task", "")

        # Map agent types to planner actions
        if agent == "ops":
            # Ops tasks typically involve running commands or checking status
            action = "run_command"
        else:
            # Self tasks are text generation or memory operations
            action = "generate_text"

        return {
            "id": subtask.get("id", 1),
            "action": action,
            "description": task_text,
            "input": task_text,
            "depends_on": subtask.get("depends_on", []),
        }

    def _build_plan(self, decomposition: dict[str, Any]) -> dict[str, Any]:
        """Build a planner-compatible plan from orchestrator decomposition."""
        steps = [
            self._subtask_to_plan_step(st)
            for st in decomposition.get("subtasks", [])
        ]
        return {
            "plan_id": f"orch-{id(decomposition)}",
            "goal": decomposition.get("goal", ""),
            "steps": steps,
            "status": "created",
        }

    def _synthesize(self, goal: str, execution_result: dict[str, Any]) -> str:
        """Synthesize a unified response from execution results."""
        results_text: list[str] = []
        for step_out in execution_result.get("results", []):
            step = step_out.get("step", {})
            output = step_out.get("output", {})
            result = output.get("result", output.get("error", "no result"))
            results_text.append(
                f"[{step.get('description', 'step')}]: {str(result)[:1000]}"
            )

        combined = "\n".join(results_text)

        try:
            result = self.run_chat_fn(
                "fallback_general",
                [
                    {
                        "role": "system",
                        "content": (
                            "Synthesize the following results into a clear, "
                            "unified response for the user."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Goal: {goal}\n\nResults:\n{combined}"[:4000],
                    },
                ],
                max_tokens=2000,
            )
            return result["text"]
        except Exception:
            logger.warning("Synthesis LLM call failed; returning raw results")
            return combined
