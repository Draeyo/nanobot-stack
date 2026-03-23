"""Pipeline explain mode — transparency for RAG responses.

When enabled, collects and returns details about each pipeline step
(classification, rewriting, retrieval, reranking, generation) so the
user can understand how the answer was produced.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("rag-bridge.explain")

EXPLAIN_ENABLED = os.getenv("EXPLAIN_ENABLED", "true").lower() == "true"


class PipelineExplainer:
    """Collects pipeline step details for a single request."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and EXPLAIN_ENABLED
        self.steps: list[dict[str, Any]] = []
        self._start_time = time.monotonic()
        self._step_start: float | None = None
        self._current_step: str = ""

    def begin_step(self, name: str, details: dict[str, Any] | None = None) -> None:
        """Start tracking a pipeline step."""
        if not self.enabled:
            return
        self._current_step = name
        self._step_start = time.monotonic()
        if details:
            logger.debug("Explain [%s]: %s", name, details)

    def end_step(self, name: str, result: dict[str, Any] | None = None) -> None:
        """Finish a pipeline step and record it."""
        if not self.enabled:
            return
        elapsed = 0.0
        if self._step_start is not None:
            elapsed = round(time.monotonic() - self._step_start, 3)

        step_info: dict[str, Any] = {
            "step": name,
            "elapsed_seconds": elapsed,
        }
        if result:
            step_info["result"] = result
        self.steps.append(step_info)
        self._step_start = None

    def add_detail(self, key: str, value: Any) -> None:
        """Add a detail to the current step."""
        if not self.enabled or not self.steps:
            return
        self.steps[-1].setdefault("details", {})[key] = value

    def get_explanation(self) -> dict[str, Any]:
        """Return the full pipeline explanation."""
        if not self.enabled:
            return {}
        total_elapsed = round(time.monotonic() - self._start_time, 3)
        return {
            "explain": True,
            "total_elapsed_seconds": total_elapsed,
            "pipeline_steps": self.steps,
            "step_count": len(self.steps),
        }


def format_explanation_text(explanation: dict[str, Any]) -> str:
    """Format an explanation as human-readable text."""
    if not explanation or not explanation.get("explain"):
        return ""

    lines = ["## Pipeline Explanation", ""]
    total = explanation.get("total_elapsed_seconds", 0)
    lines.append(f"Total processing time: {total}s")
    lines.append("")

    for step in explanation.get("pipeline_steps", []):
        name = step.get("step", "unknown")
        elapsed = step.get("elapsed_seconds", 0)
        lines.append(f"### {name} ({elapsed}s)")

        result = step.get("result", {})
        if result:
            for k, v in result.items():
                if isinstance(v, (list, dict)):
                    lines.append(f"- **{k}**: {len(v)} items" if isinstance(v, list) else f"- **{k}**: {v}")
                else:
                    lines.append(f"- **{k}**: {v}")

        details = step.get("details", {})
        for k, v in details.items():
            lines.append(f"- {k}: {v}")

        lines.append("")

    return "\n".join(lines)
