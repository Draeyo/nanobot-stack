"""Ops/SysAdmin agent — a personal SRE.

Handles server monitoring, diagnostics, maintenance, log analysis,
and infrastructure tasks. Consults runbooks before taking action.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from .base import AgentBase, AgentResult

logger = logging.getLogger("rag-bridge.agents.ops")

# Extended read-only commands for ops diagnostics
OPS_EXTRA_COMMANDS: dict[str, bool | list[str]] = {
    "ss": True,
    "ps": ["aux"],
    "top": ["-bn1"],
    "netstat": ["-tlnp"],
    "lsof": ["-i"],
    "du": ["-sh"],
    "last": True,
    "w": True,
}

OPS_SYSTEM_PROMPT = """\
You are an Ops/SysAdmin agent — a personal SRE for a self-hosted server.

Your capabilities:
- Run diagnostic commands (systemctl, journalctl, df, free, uptime, ss, ps, top, du, etc.)
- Analyze logs and system metrics
- Identify issues and suggest fixes
- Execute approved maintenance actions (via trust engine)

Before taking action, always:
1. Diagnose first (gather information)
2. Explain what you found
3. Suggest a course of action
4. Only execute if explicitly asked or if trust level allows auto-execution

Respond concisely and technically. Focus on actionable insights."""


class OpsAgent(AgentBase):
    """Server monitoring, diagnostics, and maintenance — a personal SRE."""

    name: str = "ops"
    description: str = (
        "Server monitoring, diagnostics, and maintenance — a personal SRE"
    )
    tools: list[str] = ["run_command", "web_fetch", "notify", "search_memory"]
    max_steps: int = 8

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
        """Execute an ops task with diagnostic-first approach."""
        system_prompt = self._build_system_prompt(context)

        # Step 1: Plan diagnostics
        diag_plan = self._plan_diagnostics(task, system_prompt)

        # Step 2: Execute diagnostic commands
        diag_results: list[dict[str, Any]] = []
        for cmd in diag_plan:
            result = self._run_diagnostic(cmd)
            diag_results.append(result)
            self._log_action(
                "run_command",
                {"command": cmd},
                str(result.get("stdout", ""))[:200],
            )

        # Step 3: Analyze and respond
        analysis = self._analyze(task, diag_results, system_prompt)

        return self._make_result("completed", analysis)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self, context: dict[str, Any] | None = None
    ) -> str:
        """Build system prompt, optionally enriched with runbook context."""
        runbook_context = ""
        if context and context.get("runbooks"):
            runbook_context = (
                f"\n\nRelevant runbooks:\n{context['runbooks'][:2000]}"
            )
        return OPS_SYSTEM_PROMPT + runbook_context

    def _plan_diagnostics(self, task: str, system_prompt: str) -> list[str]:
        """Determine which diagnostic commands to run."""
        try:
            result = self.run_chat_fn(
                "incident_triage",
                [
                    {
                        "role": "system",
                        "content": (
                            system_prompt
                            + "\n\nGiven the task, list diagnostic commands "
                            "to run (max 5). Return ONLY a JSON array of "
                            "command strings."
                        ),
                    },
                    {"role": "user", "content": task[:2000]},
                ],
                json_mode=True,
                max_tokens=300,
            )
            commands = json.loads(result["text"])
            if isinstance(commands, list):
                return [str(c) for c in commands[:5]]
            return []
        except Exception as exc:
            logger.warning("Ops diagnostic planning failed: %s", exc)
            return []

    def _run_diagnostic(self, command: str) -> dict[str, Any]:
        """Run a diagnostic command via the tool registry."""
        shell_fn = self.tool_registry.get(
            "run_command"
        ) or self.tool_registry.get("shell_fn")
        if not shell_fn:
            return {"ok": False, "error": "shell not available"}
        return shell_fn(command)

    def _analyze(
        self,
        task: str,
        diag_results: list[dict[str, Any]],
        system_prompt: str,
    ) -> str:
        """Analyze diagnostic results and provide recommendations."""
        results_text = "\n".join(
            f"$ {r.get('command', '?')}\n"
            f"{r.get('stdout', r.get('error', ''))[:1000]}"
            for r in diag_results
        )
        try:
            result = self.run_chat_fn(
                "incident_triage",
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Task: {task}\n\n"
                            f"Diagnostic results:\n{results_text}"
                        )[:4000],
                    },
                ],
                max_tokens=2000,
            )
            return result["text"]
        except Exception as exc:
            logger.warning("Ops analysis failed: %s", exc)
            return (
                f"Diagnostic results collected but analysis failed: {exc}\n\n"
                f"Raw results:\n{results_text[:2000]}"
            )
