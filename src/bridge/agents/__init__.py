"""Sub-agent registry for nanobot-stack.

Available agents are registered here and can be looked up by name.
The orchestrator uses this registry to delegate tasks.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import AgentBase

logger = logging.getLogger("rag-bridge.agents")

AGENT_REGISTRY: dict[str, type[AgentBase]] = {}


def register_agent(name: str, agent_cls: type[AgentBase]) -> None:
    """Register an agent class in the registry."""
    AGENT_REGISTRY[name] = agent_cls
    logger.debug("Registered agent: %s", name)


def get_agent_class(name: str) -> type[AgentBase] | None:
    """Look up an agent class by name."""
    return AGENT_REGISTRY.get(name)


def list_agents() -> list[dict[str, str]]:
    """Return a summary of all registered agents."""
    return [
        {"name": name, "description": cls.description}
        for name, cls in AGENT_REGISTRY.items()
    ]


def _register_defaults() -> None:
    """Register built-in agents. Called on import."""
    try:
        from .orchestrator import OrchestratorAgent  # noqa: WPS433

        register_agent("orchestrator", OrchestratorAgent)
    except ImportError:
        pass
    try:
        from .ops_agent import OpsAgent  # noqa: WPS433

        register_agent("ops", OpsAgent)
    except ImportError:
        pass


_register_defaults()
