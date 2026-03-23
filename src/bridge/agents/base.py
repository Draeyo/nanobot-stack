"""Agent base class and result type for the sub-agent architecture.

Provides a minimal agent abstraction: receive a task, use tools, produce a result.
Agents are composed by the orchestrator to handle complex multi-domain tasks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.agents")


@dataclass
class AgentResult:
    """Outcome returned by every agent invocation."""

    status: str  # 'completed', 'needs_approval', 'failed', 'delegated'
    output: str  # the agent's response text
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    cost_tokens: int = 0
    sub_results: list[AgentResult] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)


class AgentBase:
    """Minimal agent abstraction.

    Subclasses override :meth:`run` (and optionally
    :meth:`_build_system_prompt`) to implement domain-specific behaviour.
    """

    name: str = "base"
    description: str = "Base agent"
    max_steps: int = 10

    def __init__(
        self,
        run_chat_fn: Callable[..., Any],
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        trust_engine: Any = None,
    ) -> None:
        """Initialise the agent.

        Args:
            run_chat_fn: The bridge's ``run_chat_task`` function for LLM calls.
            tool_registry: Dict mapping tool names to callables.
            trust_engine: Optional trust engine module for action gating.
        """
        self.run_chat_fn = run_chat_fn
        self.tool_registry: dict[str, Callable[..., Any]] = tool_registry or {}
        self.trust_engine = trust_engine
        self.tools: list[str] = list(getattr(self.__class__, "tools", []))
        self.trust_overrides: dict[str, str] = dict(getattr(self.__class__, "trust_overrides", {}))
        self._actions_log: list[dict[str, Any]] = []
        self._total_tokens: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Execute the agent's reasoning loop. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement run()")

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    def _build_system_prompt(self, context: dict[str, Any] | None = None) -> str:  # pylint: disable=unused-argument
        """Build the agent-specific system prompt. Override for specialisation."""
        return f"You are the {self.name} agent. {self.description}"

    def _log_action(
        self, action: str, params: dict[str, Any] | None = None, result: str = ""
    ) -> None:
        """Record an action for the result log."""
        self._actions_log.append(
            {
                "action": action,
                "params": params or {},
                "result": result[:500],
            }
        )

    def _call_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        """Call a tool from the registry, respecting trust overrides."""
        if tool_name not in self.tool_registry:
            return {"ok": False, "error": f"tool '{tool_name}' not available"}

        if self.trust_engine:
            trust_level = self.trust_overrides.get(tool_name)
            if trust_level == "blocked":
                logger.warning("Blocked tool call: %s (agent=%s)", tool_name, self.name)
                return {"ok": False, "error": f"tool '{tool_name}' is blocked for this agent"}

        fn = self.tool_registry[tool_name]
        return fn(**kwargs)

    def _make_result(
        self,
        status: str,
        output: str,
        sub_results: list[AgentResult] | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Build an :class:`AgentResult` from the current run state."""
        return AgentResult(
            status=status,
            output=output,
            actions_taken=list(self._actions_log),
            cost_tokens=self._total_tokens,
            sub_results=sub_results or [],
            artifacts=artifacts or {},
        )
