"""Orchestrator agent — true hierarchical sub-agent delegation.

Decomposes complex tasks into sub-tasks, spawns built-in or user-created
sub-agents, tracks their execution, interprets results, and synthesizes
a unified response.

Sub-agents can be:
  - Built-in: ops, web_search, browser (from AGENT_REGISTRY)
  - Custom: user-created via admin UI (from custom_agents SQLite)
  - Self: handled directly by the orchestrator

Model selection:
  - By default, the orchestrator picks the model via adaptive routing
  - Custom agents can have a forced_model that overrides routing
  - Users can request a specific model via the task context
"""
from __future__ import annotations

import asyncio
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
- "self": handle directly (text generation, memory lookup, factual answers)
- "ops": server/infrastructure tasks (monitoring, diagnostics, logs)
- "web_search": web search queries
- "browser": web page automation (navigate, extract, fill forms)
{custom_agents_section}
Return ONLY JSON:
{{
  "goal": "one-line description",
  "subtasks": [
    {{"id": 1, "agent": "self", "task": "what to do", "depends_on": [], "priority": "high|medium|low"}}
  ],
  "estimated_cost": "low|medium|high"
}}

Keep to 1-5 subtasks. Prefer fewer. Use specialized agents when their expertise matches.\
"""


class OrchestratorAgent(AgentBase):
    """Decomposes complex tasks and delegates to real sub-agents."""

    name: str = "orchestrator"
    description: str = "Decomposes complex tasks and delegates to specialist sub-agents"
    max_steps: int = 15

    def __init__(
        self,
        run_chat_fn: Callable[..., Any],
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        trust_engine: Any = None,
    ) -> None:
        super().__init__(run_chat_fn, tool_registry, trust_engine)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AgentResult:
        if not ORCHESTRATOR_ENABLED:
            return self._make_result("failed", "Orchestrator is disabled")

        context = context or {}
        forced_model = context.get("forced_model", "")

        # Step 1: Decompose task into subtasks
        decomposition = self._decompose(task, context)
        if not decomposition:
            # Simple fallback: handle directly
            return await self._handle_self(task, forced_model)

        subtasks = decomposition.get("subtasks", [])
        goal = decomposition.get("goal", task)

        if len(subtasks) <= 1 and subtasks and subtasks[0].get("agent") == "self":
            return await self._handle_self(subtasks[0].get("task", task), forced_model)

        # Step 2: Execute subtasks — spawn real sub-agents
        sub_results: list[dict[str, Any]] = []
        completed: dict[int, str] = {}

        for subtask in self._topo_sort(subtasks):
            st_id = subtask.get("id", 0)
            agent_name = subtask.get("agent", "self")
            st_task = subtask.get("task", "")

            # Inject results of dependencies into context
            deps = subtask.get("depends_on", [])
            dep_context = {f"step_{d}": completed.get(d, "") for d in deps if d in completed}
            if dep_context:
                st_task += f"\n\nPrevious results: {json.dumps(dep_context)[:2000]}"

            self._log_action("delegate", {"agent": agent_name, "task": st_task[:200]}, "started")

            result = await self._run_sub_agent(agent_name, st_task, context, forced_model)
            sub_results.append({
                "id": st_id, "agent": agent_name, "task": subtask.get("task", ""),
                "status": result.status, "output": result.output[:2000],
                "tokens": result.cost_tokens,
            })
            completed[st_id] = result.output[:1000]
            self._total_tokens += result.cost_tokens
            self._log_action("delegate_done", {"agent": agent_name}, result.status)

        # Step 3: Synthesize
        output = self._synthesize(goal, sub_results, forced_model)
        return self._make_result("completed", output, sub_results=[
            AgentResult(status=r["status"], output=r["output"], cost_tokens=r["tokens"])
            for r in sub_results
        ])

    # ------------------------------------------------------------------
    # Sub-agent execution
    # ------------------------------------------------------------------

    async def _run_sub_agent(
        self, agent_name: str, task: str, context: dict[str, Any], forced_model: str
    ) -> AgentResult:
        """Spawn and run a sub-agent by name."""

        # 1. Check user-created custom agents
        try:
            from custom_agents import get_custom_agent_by_name
            custom = get_custom_agent_by_name(agent_name)
            if custom:
                return await self._run_custom_agent(custom, task, forced_model)
        except ImportError:
            pass

        # 2. Check built-in agent registry
        from agents import get_agent_class
        agent_cls = get_agent_class(agent_name)
        if agent_cls and agent_name != "orchestrator":
            try:
                agent = agent_cls(
                    run_chat_fn=self._make_model_fn(forced_model),
                    tool_registry=self.tool_registry,
                    trust_engine=self.trust_engine,
                )
                return await agent.run(task, context)
            except Exception as exc:
                logger.warning("Sub-agent %s failed: %s", agent_name, exc)
                return AgentResult(status="failed", output=f"Agent {agent_name} error: {exc}")

        # 3. Fallback: handle as "self" (direct LLM)
        return await self._handle_self(task, forced_model)

    async def _run_custom_agent(
        self, agent_def: dict[str, Any], task: str, orchestrator_forced_model: str
    ) -> AgentResult:
        """Run a user-created custom sub-agent."""
        system_prompt = agent_def.get("system_prompt", "")
        # Model priority: agent-level forced > orchestrator-level forced > adaptive
        model_override = agent_def.get("forced_model", "") or orchestrator_forced_model
        agent_tools = json.loads(agent_def.get("tools", "[]")) if isinstance(agent_def.get("tools"), str) else agent_def.get("tools", [])

        run_fn = self._make_model_fn(model_override)

        # Build the sub-agent prompt
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})

        # If agent has tools, let it plan and execute
        if agent_tools and self.tool_registry:
            tool_descriptions = ", ".join(agent_tools)
            messages[0 if system_prompt else -1]["content"] = (
                (messages[0]["content"] if system_prompt else "") +
                f"\n\nAvailable tools: {tool_descriptions}. "
                "Respond with a JSON plan if you need to use tools, or answer directly."
            ).strip()

        try:
            result = run_fn(
                "fallback_general", messages,
                max_tokens=2000,
            )
            output = result.get("text", str(result))

            # Try to execute tool calls from the response
            if agent_tools and self.tool_registry:
                output = self._maybe_execute_tools(output, agent_tools)

            tokens = sum(a.get("input_tokens", 0) + a.get("output_tokens", 0)
                         for a in result.get("attempts", []))
            return AgentResult(status="completed", output=output, cost_tokens=tokens)
        except Exception as exc:
            return AgentResult(status="failed", output=f"Custom agent error: {exc}")

    async def _handle_self(self, task: str, forced_model: str) -> AgentResult:
        """Handle a task directly via LLM."""
        run_fn = self._make_model_fn(forced_model)
        try:
            result = run_fn(
                "fallback_general",
                [{"role": "user", "content": task}],
                max_tokens=2000,
            )
            tokens = sum(a.get("input_tokens", 0) + a.get("output_tokens", 0)
                         for a in result.get("attempts", []))
            return AgentResult(status="completed", output=result["text"], cost_tokens=tokens)
        except Exception as exc:
            return AgentResult(status="failed", output=f"Self-handle error: {exc}")

    # ------------------------------------------------------------------
    # Model override support
    # ------------------------------------------------------------------

    def _make_model_fn(self, forced_model: str) -> Callable:
        """Return a run_chat_fn that forces a specific model, or the default."""
        if not forced_model:
            return self.run_chat_fn

        def _forced_run(task_type, messages, **kwargs):
            # Override: use the forced model directly via litellm
            try:
                from litellm import completion as litellm_completion
                resp = litellm_completion(
                    model=forced_model,
                    messages=messages,
                    max_tokens=kwargs.get("max_tokens", 2000),
                    temperature=kwargs.get("temperature", 0.3),
                )
                text = resp.choices[0].message.content or ""
                return {
                    "text": text,
                    "attempts": [{"model": forced_model, "status": "ok",
                                  "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                                  "output_tokens": resp.usage.completion_tokens if resp.usage else 0}],
                    "model": forced_model,
                }
            except Exception as exc:
                logger.warning("Forced model %s failed, falling back to default: %s", forced_model, exc)
                return self.run_chat_fn(task_type, messages, **kwargs)

        return _forced_run

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decompose(
        self, task: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        context_str = ""
        if context:
            context_str = f"\nContext: {json.dumps(context)[:1000]}"

        # Build dynamic prompt with custom agents
        custom_section = ""
        try:
            from custom_agents import list_custom_agents
            customs = [a for a in list_custom_agents() if a.get("enabled")]
            if customs:
                lines = [f'- "{a["name"]}": {a["description"]}' for a in customs]
                custom_section = "\nCustom specialist agents:\n" + "\n".join(lines) + "\n"
        except ImportError:
            pass

        prompt = ORCHESTRATE_PROMPT.format(custom_agents_section=custom_section)

        try:
            result = self.run_chat_fn(
                "tool_planning",
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"{task}{context_str}"[:4000]},
                ],
                json_mode=True,
                max_tokens=1000,
            )
            return json.loads(result["text"])
        except Exception as exc:
            logger.warning("Decomposition failed: %s", exc)
            return None

    def _synthesize(
        self, goal: str, sub_results: list[dict[str, Any]], forced_model: str = ""
    ) -> str:
        parts = []
        for r in sub_results:
            parts.append(f"[{r.get('agent', 'unknown')}: {r.get('task', '')[:100]}]\n{r.get('output', '')[:1500]}")
        combined = "\n\n".join(parts)

        run_fn = self._make_model_fn(forced_model)
        try:
            result = run_fn(
                "fallback_general",
                [
                    {"role": "system", "content": "Synthesize sub-agent results into a clear, unified response. Reference which agent produced what when relevant."},
                    {"role": "user", "content": f"Goal: {goal}\n\nSub-agent results:\n{combined}"[:6000]},
                ],
                max_tokens=2000,
            )
            return result["text"]
        except Exception:
            return combined

    def _maybe_execute_tools(self, response: str, allowed_tools: list[str]) -> str:
        """If the response contains tool call JSON, execute and append results."""
        try:
            data = json.loads(response)
            if isinstance(data, dict) and "tool" in data:
                tool_name = data["tool"]
                if tool_name in allowed_tools and tool_name in self.tool_registry:
                    params = data.get("params", {})
                    result = self.tool_registry[tool_name](**params) if isinstance(params, dict) else self.tool_registry[tool_name](params)
                    return f"{response}\n\nTool result: {str(result)[:2000]}"
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        return response

    def _topo_sort(self, subtasks: list[dict]) -> list[dict]:
        """Topological sort by depends_on for execution order."""
        by_id = {st.get("id", i): st for i, st in enumerate(subtasks)}
        visited: set[int] = set()
        result: list[dict] = []

        def visit(st_id: int) -> None:
            if st_id in visited:
                return
            visited.add(st_id)
            st = by_id.get(st_id)
            if not st:
                return
            for dep in st.get("depends_on", []):
                visit(dep)
            result.append(st)

        for st_id in by_id:
            visit(st_id)
        return result
