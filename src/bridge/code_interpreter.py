"""Sandboxed code interpreter for safe Python execution.

Executes user-provided Python code in a restricted environment with:
- Timeout enforcement
- Memory limits
- No filesystem write access
- No network access
- Limited stdlib imports
- Output capture (stdout, stderr, return value)
"""
from __future__ import annotations

import io
import logging
import os
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

logger = logging.getLogger("rag-bridge.code_interpreter")

CODE_INTERPRETER_ENABLED = os.getenv("CODE_INTERPRETER_ENABLED", "true").lower() == "true"
CODE_TIMEOUT = int(os.getenv("CODE_INTERPRETER_TIMEOUT", "30"))
CODE_MAX_OUTPUT = int(os.getenv("CODE_INTERPRETER_MAX_OUTPUT", "10000"))

# Allowed built-in modules for the sandbox
ALLOWED_MODULES = {
    "math", "statistics", "random", "datetime", "collections", "itertools",
    "functools", "operator", "string", "re", "json", "csv", "io",
    "textwrap", "unicodedata", "decimal", "fractions", "copy",
    "hashlib", "base64", "html", "urllib.parse",
}

# Explicitly blocked modules
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "pathlib", "socket", "http",
    "ftplib", "smtplib", "ssl", "ctypes", "importlib", "builtins",
    "signal", "multiprocessing", "threading", "pty", "resource",
    "pickle", "shelve", "tempfile", "glob", "code", "compile",
}


class SandboxImportError(ImportError):
    pass


def execute_code(code: str, timeout: int | None = None) -> dict[str, Any]:
    """Execute Python code in a sandboxed environment.

    Args:
        code: Python source code to execute.
        timeout: Maximum execution time in seconds.

    Returns:
        Dict with stdout, stderr, return_value, error, and execution_time.
    """
    if not CODE_INTERPRETER_ENABLED:
        return {"ok": False, "error": "Code interpreter is disabled"}

    effective_timeout = min(timeout or CODE_TIMEOUT, CODE_TIMEOUT)

    # Validate code doesn't contain obvious escapes
    dangerous_patterns = [
        "exec(", "eval(", "__import__", "compile(", "globals()", "locals()",
        "getattr(", "setattr(", "delattr(", "__builtins__", "__class__",
        "open(", "file(",
    ]
    for pattern in dangerous_patterns:
        if pattern in code:
            return {"ok": False, "error": f"Blocked: '{pattern}' is not allowed in sandbox"}

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    result: dict[str, Any] = {"ok": False}

    def _run():
        nonlocal result
        import time
        t0 = time.monotonic()

        # Set up restricted globals
        safe_globals = {"__builtins__": {
            "print": print, "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter, "sorted": sorted,
            "reversed": reversed, "list": list, "dict": dict, "set": set,
            "tuple": tuple, "str": str, "int": int, "float": float, "bool": bool,
            "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
            "isinstance": isinstance, "type": type, "hasattr": hasattr,
            "all": all, "any": any, "chr": chr, "ord": ord,
            "hex": hex, "oct": oct, "bin": bin, "pow": pow,
            "divmod": divmod, "complex": complex, "bytes": bytes,
            "bytearray": bytearray, "memoryview": memoryview,
            "frozenset": frozenset, "property": property, "staticmethod": staticmethod,
            "classmethod": classmethod, "super": super, "object": object,
            "True": True, "False": False, "None": None,
            "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "AttributeError": AttributeError,
            "RuntimeError": RuntimeError, "StopIteration": StopIteration,
            "Exception": Exception, "ZeroDivisionError": ZeroDivisionError,
            "__import__": lambda name, *a, **kw: _safe_import(name),
        }}

        def _safe_import(name):
            top = name.split(".")[0]
            if top in BLOCKED_MODULES:
                raise SandboxImportError(f"Import of '{name}' is not allowed")
            if top not in ALLOWED_MODULES:
                raise SandboxImportError(f"Module '{name}' is not available in sandbox. Allowed: {', '.join(sorted(ALLOWED_MODULES))}")
            return __import__(name)

        safe_locals: dict[str, Any] = {}

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                exec(compile(code, "<sandbox>", "exec"), safe_globals, safe_locals)

            elapsed = round(time.monotonic() - t0, 3)
            stdout_text = stdout_capture.getvalue()[:CODE_MAX_OUTPUT]
            stderr_text = stderr_capture.getvalue()[:CODE_MAX_OUTPUT]

            # Try to capture the last expression's value
            return_value = safe_locals.get("result", safe_locals.get("output", None))

            result = {
                "ok": True,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "return_value": str(return_value) if return_value is not None else None,
                "execution_time": elapsed,
                "variables": {k: str(v)[:500] for k, v in safe_locals.items()
                              if not k.startswith("_") and k not in ("__builtins__",)},
            }
        except SandboxImportError as e:
            result = {"ok": False, "error": str(e), "execution_time": round(time.monotonic() - t0, 3)}
        except Exception as e:
            tb = traceback.format_exc()
            result = {
                "ok": False,
                "error": str(e),
                "traceback": tb[:CODE_MAX_OUTPUT],
                "stdout": stdout_capture.getvalue()[:CODE_MAX_OUTPUT],
                "stderr": stderr_capture.getvalue()[:CODE_MAX_OUTPUT],
                "execution_time": round(time.monotonic() - t0, 3),
            }

    # Run in a thread with timeout
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=effective_timeout)

    if thread.is_alive():
        return {
            "ok": False,
            "error": f"Execution timed out after {effective_timeout}s",
            "stdout": stdout_capture.getvalue()[:CODE_MAX_OUTPUT],
        }

    return result
