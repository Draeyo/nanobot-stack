"""Dynamic tools: restricted shell, web fetch, notifications.

These are executed server-side by the bridge and exposed via the MCP server.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

import httpx

logger = logging.getLogger("rag-bridge.tools")

SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "15"))
WEB_FETCH_TIMEOUT = int(os.getenv("WEB_FETCH_TIMEOUT", "30"))
WEB_FETCH_MAX_CHARS = int(os.getenv("WEB_FETCH_MAX_CHARS", "15000"))
NOTIFICATION_WEBHOOK_URL = os.getenv("NOTIFICATION_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Shell command allow-list (read-only, safe commands only)
# ---------------------------------------------------------------------------
# True = any subcommand allowed; list = only those subcommands allowed
ALLOWED_SHELL_COMMANDS: dict[str, bool | list[str]] = {
    "systemctl": ["status", "is-active", "is-enabled", "list-timers"],
    "journalctl": True,
    "openssl": ["s_client", "x509"],
    "curl": True,
    "dig": True,
    "host": True,
    "df": True,
    "uptime": True,
    "free": True,
    "uname": True,
    "cat": ["/etc/os-release"],
    "qdrant": ["--version"],
    "docker": ["ps", "compose", "images"],
}


def validate_shell_command(cmd: str) -> tuple[bool, str]:
    """Check if a shell command is in the allow-list. Returns (allowed, reason)."""
    parts = cmd.strip().split()
    if not parts:
        return False, "empty command"

    binary = parts[0].split("/")[-1]  # handle full paths

    if binary not in ALLOWED_SHELL_COMMANDS:
        return False, f"binary '{binary}' not in allow-list"

    allowed = ALLOWED_SHELL_COMMANDS[binary]
    if allowed is True:
        return True, "allowed"

    # Check subcommand
    if isinstance(allowed, list):
        if len(parts) > 1 and parts[1] in allowed:
            return True, "allowed"
        if len(parts) == 1:
            return True, "allowed (no subcommand)"
        return False, f"subcommand '{parts[1]}' not allowed for '{binary}'"

    return False, "unknown allow-list format"


def run_shell_command(command: str) -> dict[str, Any]:
    """Execute a pre-approved read-only shell command."""
    allowed, reason = validate_shell_command(command)
    if not allowed:
        return {"ok": False, "error": f"Command not allowed: {reason}", "command": command}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
            env={**os.environ, "LANG": "C.UTF-8"},
            check=False,
        )
        return {
            "ok": True,
            "command": command,
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": command, "error": f"timeout after {SHELL_TIMEOUT}s"}
    except Exception as e:
        return {"ok": False, "command": command, "error": str(e)}


async def web_fetch(url: str) -> dict[str, Any]:
    """Fetch a web page and extract text content."""
    if not re.match(r"^https?://", url):
        return {"ok": False, "url": url, "error": "URL must start with http:// or https://"}

    try:
        timeout = httpx.Timeout(WEB_FETCH_TIMEOUT, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "nanobot-rag-bridge/7.0"})
            r.raise_for_status()

            content_type = r.headers.get("content-type", "")
            if "text/html" in content_type:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, "html.parser")
                    # Remove scripts and styles
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    text = soup.get_text("\n", strip=True)
                except ImportError:
                    text = r.text
            else:
                text = r.text

            text = text[:WEB_FETCH_MAX_CHARS]
            return {"ok": True, "url": url, "text": text, "content_type": content_type, "chars": len(text)}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


async def send_notification(message: str, title: str = "nanobot", level: str = "info") -> dict[str, Any]:
    """Send a notification via webhook (supports generic JSON webhook, Telegram-style, ntfy-style)."""
    webhook_url = NOTIFICATION_WEBHOOK_URL
    if not webhook_url:
        return {"ok": False, "error": "NOTIFICATION_WEBHOOK_URL not configured"}

    payload: dict[str, Any]
    headers: dict[str, str] = {"Content-Type": "application/json"}

    # Detect webhook type from URL
    if "ntfy" in webhook_url or webhook_url.rstrip("/").split("/")[-1].isalpha():
        # ntfy.sh style
        headers = {"Title": title, "Priority": "default" if level == "info" else "high"}
        payload_raw = message
        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(webhook_url, content=payload_raw, headers=headers)
                r.raise_for_status()
                return {"ok": True, "service": "ntfy", "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        # Generic JSON webhook (Slack/Discord/Telegram bot API compatible)
        payload = {"text": f"**[{title}]** ({level})\n{message}"}
        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(webhook_url, json=payload, headers=headers)
                r.raise_for_status()
                return {"ok": True, "service": "webhook", "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
