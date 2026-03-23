"""Plugin system for extensible tool capabilities.

Plugins are Python modules in the plugins/ directory. Each plugin can expose:
- tools: functions decorated with @plugin_tool that become MCP tools
- hooks: functions that run on specific events (ingest, search, remember)
- endpoints: FastAPI router with additional HTTP endpoints

Plugin discovery is automatic at startup, with hot-reload on SIGHUP.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import pathlib
import sys
import threading
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.plugins")

PLUGINS_DIR = pathlib.Path(os.getenv("PLUGINS_DIR", "/opt/nanobot-stack/rag-bridge/plugins"))
PLUGINS_ENABLED = os.getenv("PLUGINS_ENABLED", "true").lower() == "true"


class PluginTool:
    """Descriptor for a plugin-provided tool."""
    def __init__(self, name: str, description: str, fn: Callable, plugin_name: str):
        self.name = name
        self.description = description
        self.fn = fn
        self.plugin_name = plugin_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "plugin": self.plugin_name,
        }


class PluginHook:
    """A hook registered by a plugin."""
    def __init__(self, event: str, fn: Callable, plugin_name: str, priority: int = 100):
        self.event = event
        self.fn = fn
        self.plugin_name = plugin_name
        self.priority = priority


class Plugin:
    """Represents a loaded plugin."""
    def __init__(self, name: str, module: Any):
        self.name = name
        self.module = module
        self.tools: list[PluginTool] = []
        self.hooks: list[PluginHook] = []
        self.router = None  # FastAPI router, if any
        self.enabled = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "tools": [t.to_dict() for t in self.tools],
            "hooks": [{"event": h.event, "priority": h.priority} for h in self.hooks],
            "has_router": self.router is not None,
        }


class PluginRegistry:
    """Central registry for all loaded plugins."""

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self._tools: dict[str, PluginTool] = {}
        self._hooks: dict[str, list[PluginHook]] = {}
        self._lock = threading.Lock()

    def discover_and_load(self) -> list[str]:
        """Discover and load all plugins from the plugins directory."""
        if not PLUGINS_ENABLED:
            logger.info("Plugin system disabled")
            return []

        if not PLUGINS_DIR.is_dir():
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            logger.info("Created plugins directory: %s", PLUGINS_DIR)
            return []

        loaded = []
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            try:
                self._load_plugin(name, path)
                loaded.append(name)
                logger.info("Loaded plugin: %s", name)
            except Exception as exc:
                logger.warning("Failed to load plugin %s: %s", name, exc)

        return loaded

    def _load_plugin(self, name: str, path: pathlib.Path) -> None:
        """Load a single plugin module."""
        spec = importlib.util.spec_from_file_location(f"plugins.{name}", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin spec from {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"plugins.{name}"] = module
        spec.loader.exec_module(module)

        plugin = Plugin(name, module)

        # Discover tools (functions with _plugin_tool attribute)
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if callable(obj) and hasattr(obj, "_plugin_tool"):
                tool_meta = obj._plugin_tool
                tool = PluginTool(
                    name=tool_meta.get("name", attr_name),
                    description=tool_meta.get("description", ""),
                    fn=obj,
                    plugin_name=name,
                )
                plugin.tools.append(tool)

        # Discover hooks (functions with _plugin_hook attribute)
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if callable(obj) and hasattr(obj, "_plugin_hook"):
                hook_meta = obj._plugin_hook
                hook = PluginHook(
                    event=hook_meta.get("event", ""),
                    fn=obj,
                    plugin_name=name,
                    priority=hook_meta.get("priority", 100),
                )
                plugin.hooks.append(hook)

        # Discover router (module-level 'router' attribute)
        if hasattr(module, "router"):
            plugin.router = module.router

        with self._lock:
            self._plugins[name] = plugin
            for tool in plugin.tools:
                self._tools[tool.name] = tool
            for hook in plugin.hooks:
                self._hooks.setdefault(hook.event, []).append(hook)
                self._hooks[hook.event].sort(key=lambda h: h.priority)

    def reload_plugin(self, name: str) -> bool:
        """Reload a specific plugin."""
        path = PLUGINS_DIR / f"{name}.py"
        if not path.exists():
            return False

        # Remove old registration
        with self._lock:
            old = self._plugins.pop(name, None)
            if old:
                for tool in old.tools:
                    self._tools.pop(tool.name, None)
                for hook in old.hooks:
                    hooks_list = self._hooks.get(hook.event, [])
                    self._hooks[hook.event] = [h for h in hooks_list if h.plugin_name != name]

        try:
            self._load_plugin(name, path)
            logger.info("Reloaded plugin: %s", name)
            return True
        except Exception as exc:
            logger.warning("Failed to reload plugin %s: %s", name, exc)
            return False

    def run_tool(self, tool_name: str, **kwargs) -> Any:
        """Execute a plugin tool by name."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {"error": f"Unknown plugin tool: {tool_name}"}
        try:
            return tool.fn(**kwargs)
        except Exception as exc:
            logger.warning("Plugin tool %s failed: %s", tool_name, exc)
            return {"error": str(exc)}

    def run_hooks(self, event: str, **kwargs) -> list[Any]:
        """Run all hooks for an event, in priority order."""
        hooks = self._hooks.get(event, [])
        results = []
        for hook in hooks:
            try:
                result = hook.fn(**kwargs)
                results.append({"plugin": hook.plugin_name, "result": result})
            except Exception as exc:
                results.append({"plugin": hook.plugin_name, "error": str(exc)})
        return results

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all loaded plugins."""
        with self._lock:
            return [p.to_dict() for p in self._plugins.values()]

    def list_tools(self) -> list[dict[str, Any]]:
        """List all plugin tools."""
        with self._lock:
            return [t.to_dict() for t in self._tools.values()]

    def get_routers(self):
        """Return all plugin FastAPI routers for mounting."""
        with self._lock:
            return [(p.name, p.router) for p in self._plugins.values() if p.router]


# Decorators for plugin authors
def plugin_tool(name: str = "", description: str = ""):
    """Decorator to mark a function as a plugin tool."""
    def decorator(fn):
        fn._plugin_tool = {
            "name": name or fn.__name__,
            "description": description or fn.__doc__ or "",
        }
        return fn
    return decorator


def plugin_hook(event: str, priority: int = 100):
    """Decorator to mark a function as a plugin hook.

    Events: 'pre_search', 'post_search', 'pre_ingest', 'post_ingest',
            'pre_remember', 'post_remember', 'pre_chat', 'post_chat'.
    """
    def decorator(fn):
        fn._plugin_hook = {"event": event, "priority": priority}
        return fn
    return decorator


# Global singleton
plugin_registry = PluginRegistry()
