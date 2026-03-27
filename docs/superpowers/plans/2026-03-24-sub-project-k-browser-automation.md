# Sub-projet K — Browser Automation (Playwright) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add headless browser automation via BrowserAgent (Playwright), with trust engine integration per action type and domain allowlist

**Architecture:** BrowserAgent extends AgentBase and runs a headless Chromium session via Playwright async API. Every action (navigate/click/fill/submit) checks trust_engine.py before executing. Domain allowlist enforced via _check_domain_allowlist(). Sessions are ephemeral (new context per run, destroyed after). SQLite table browser_action_log tracks all actions with trust level.

**Tech Stack:** playwright>=1.44 (requires `playwright install chromium`), trust_engine.py (existing), AgentBase (existing), FastAPI (existing)

---

## Key Files (read before implementing)

| File | Purpose |
|------|---------|
| `src/bridge/agents/base.py` | AgentBase, AgentResult — extend these |
| `src/bridge/agents/ops_agent.py` | Agent implementation pattern |
| `src/bridge/agents/__init__.py` | AGENT_REGISTRY + `_register_defaults()` pattern |
| `src/bridge/trust_engine.py` | `check_and_execute()`, `set_trust_level()`, `get_trust_level()` |
| `migrations/015_backup_log.py` | Migration pattern: VERSION, check(), migrate(), WAL mode |
| `src/bridge/app.py` | FastAPI app startup + router mounting pattern |
| `src/bridge/requirements.txt` | Add `playwright>=1.44` here |

## Notes

- Migration number is **019** (spec-allocated; 016=memory-decay, 017 and 018 reserved for other sub-projects).
- The migration uses `browser.db` (not `scheduler.db`) — browser actions are a distinct domain.
- `browser_submit` is **NEVER auto-promoted** — its `auto_promote_after` must be set to `0` in the default policy seeding.
- `fill()` must mask the value in all logs (replace with `"***"`).
- `inner_text()` is mandatory everywhere — `inner_html()` / `content()` are forbidden (XSS prevention).
- URL validation must reject `file://`, `javascript:`, `data:` schemes before any allowlist check.
- `BROWSER_ENABLED` defaults to `false` — the agent must return `AgentResult(status="disabled")` without launching Chromium if disabled.
- The `playwright install chromium` command must be run post-install (not automatic).

---

## Task 1 — Migration `migrations/021_browser.py`

**TDD approach:** write the test first, then the migration.

### Step 1 — Write the test

Create `tests/test_migration_019.py`:

```python
"""Tests for migration 019 — browser_action_log table."""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
sys.path.insert(0, str(MIGRATIONS_DIR))


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    return tmp_path


def _load():
    # Force reimport so RAG_STATE_DIR monkeypatch is respected.
    if "021_browser" in sys.modules:
        del sys.modules["021_browser"]
    return importlib.import_module("021_browser")


def test_version(state_dir):
    m = _load()
    assert m.VERSION == 19


def test_check_returns_false_before_migration(state_dir):
    m = _load()
    assert m.check({}) is False


def test_migrate_creates_table(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_action_log'"
    ).fetchall()
    db.close()
    assert len(tables) == 1


def test_migrate_creates_indexes(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    indexes = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='browser_action_log'"
    ).fetchall()
    db.close()
    index_names = {r[0] for r in indexes}
    assert "idx_browser_action_log_session_id" in index_names
    assert "idx_browser_action_log_started_at" in index_names
    assert "idx_browser_action_log_status" in index_names


def test_check_returns_true_after_migration(state_dir):
    m = _load()
    m.migrate({})
    assert m.check({}) is True


def test_migrate_is_idempotent(state_dir):
    m = _load()
    m.migrate({})
    m.migrate({})  # must not raise
    assert m.check({}) is True


def test_table_columns(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    cols = {row[1] for row in db.execute("PRAGMA table_info(browser_action_log)").fetchall()}
    db.close()
    expected = {
        "id", "session_id", "action_type", "url", "selector",
        "status", "trust_level", "approved_by", "started_at",
        "duration_ms", "error_msg",
    }
    assert expected.issubset(cols)


def test_insert_and_read(state_dir):
    m = _load()
    m.migrate({})
    db = sqlite3.connect(str(state_dir / "browser.db"))
    db.execute(
        "INSERT INTO browser_action_log "
        "(id, session_id, action_type, url, status, trust_level, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("uuid-1", "sess-1", "navigate", "https://example.com", "ok", "auto", "2026-01-01T00:00:00Z"),
    )
    db.commit()
    row = db.execute("SELECT action_type FROM browser_action_log WHERE id='uuid-1'").fetchone()
    db.close()
    assert row[0] == "navigate"
```

- [ ] Create `tests/test_migration_019.py` with the content above.

### Step 2 — Run the test (expect failure)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_migration_019.py -v
# Expected: ModuleNotFoundError — 021_browser does not exist yet
```

- [ ] Confirm tests fail as expected (module not found).

### Step 3 — Create the migration

Create `migrations/021_browser.py`:

```python
"""021_browser — browser_action_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 21

logger = logging.getLogger("migration.v19")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "browser.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_action_log'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS browser_action_log (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                action_type TEXT NOT NULL,
                url         TEXT NOT NULL,
                selector    TEXT,
                status      TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                approved_by TEXT,
                started_at  TEXT NOT NULL,
                duration_ms INTEGER,
                error_msg   TEXT
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_session_id
            ON browser_action_log(session_id)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_started_at
            ON browser_action_log(started_at DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_browser_action_log_status
            ON browser_action_log(status)
        """)
        db.commit()
        logger.info("Migration 019: browser_action_log table created at %s", db_path)
    finally:
        db.close()
```

- [ ] Create `migrations/021_browser.py` with the content above.

### Step 4 — Run tests (expect all green)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_migration_019.py -v
# Expected: 7 passed
```

- [ ] All 7 migration tests pass.

### Step 5 — Commit

```bash
git add migrations/021_browser.py tests/test_migration_019.py
git commit -m "feat(migration): add 019 — browser_action_log table"
```

- [ ] Commit created.

---

## Task 2 — Add `playwright>=1.44` to requirements

### Step 1 — Write the test

Add to `tests/test_browser_agent.py` (create file, will grow through subsequent tasks):

```python
"""Tests for BrowserAgent — full Playwright mock suite."""
from __future__ import annotations

import importlib
import sys


def test_playwright_in_requirements():
    """playwright>=1.44 must be listed in requirements.txt."""
    req_path = (
        __import__("pathlib").Path(__file__).parent.parent
        / "src" / "bridge" / "requirements.txt"
    )
    content = req_path.read_text()
    assert "playwright" in content, "playwright not found in requirements.txt"
```

- [ ] Create `tests/test_browser_agent.py` with the content above.

### Step 2 — Run the test (expect failure)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py::test_playwright_in_requirements -v
# Expected: AssertionError — playwright not in requirements.txt
```

- [ ] Confirm test fails.

### Step 3 — Add dependency

Edit `src/bridge/requirements.txt` — append after the last line:

```
playwright>=1.44  # requires: playwright install chromium
```

- [ ] Add `playwright>=1.44` line to `src/bridge/requirements.txt`.

### Step 4 — Run test (expect green)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py::test_playwright_in_requirements -v
# Expected: 1 passed
```

- [ ] Test passes.

### Step 5 — Note on post-install

After deploying, run:

```bash
playwright install chromium
```

This is **not automatic** — it must be run once in the deployment environment (or added to Dockerfile build step for Option B, or handled by the sidecar image for Option A).

### Step 6 — Commit

```bash
git add src/bridge/requirements.txt tests/test_browser_agent.py
git commit -m "feat(browser): add playwright>=1.44 dependency"
```

- [ ] Commit created.

---

## Task 3 — `BrowserAgent` skeleton (BROWSER_ENABLED guard + env loading)

### Step 1 — Write the test

Add to `tests/test_browser_agent.py`:

```python
import os
import pytest


def _import_browser_agent():
    """Import BrowserAgent, clearing module cache first."""
    for key in list(sys.modules.keys()):
        if "browser_agent" in key:
            del sys.modules[key]
    sys.path.insert(0, str(
        __import__("pathlib").Path(__file__).parent.parent / "src" / "bridge"
    ))
    from agents.browser_agent import BrowserAgent  # noqa: PLC0415
    return BrowserAgent


def test_browser_agent_disabled_by_default(monkeypatch):
    """BrowserAgent.run() returns status='disabled' when BROWSER_ENABLED is false."""
    monkeypatch.setenv("BROWSER_ENABLED", "false")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(agent.run("navigate to example.com"))
    assert result.status == "disabled"
    assert "BROWSER_ENABLED" in result.output


def test_browser_agent_name():
    BrowserAgent = _import_browser_agent()
    assert BrowserAgent.name == "browser"


def test_browser_agent_has_correct_tools():
    BrowserAgent = _import_browser_agent()
    assert "navigate" in BrowserAgent.tools
    assert "screenshot" in BrowserAgent.tools
    assert "extract_text" in BrowserAgent.tools
    assert "click" in BrowserAgent.tools
    assert "fill" in BrowserAgent.tools
    assert "submit" in BrowserAgent.tools


def test_browser_agent_env_loading(monkeypatch):
    """Env vars are read at instantiation time."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com,docs.python.org")
    monkeypatch.setenv("BROWSER_PAGE_TIMEOUT_MS", "15000")
    monkeypatch.setenv("BROWSER_MAX_SESSION_S", "120")
    monkeypatch.setenv("BROWSER_SCREENSHOT_STORE", "true")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent.browser_enabled is True
    assert "github.com" in agent.allowed_domains
    assert "docs.python.org" in agent.allowed_domains
    assert agent.page_timeout_ms == 15000
    assert agent.max_session_s == 120
    assert agent.screenshot_store is True
```

- [ ] Add the 5 tests above to `tests/test_browser_agent.py`.

### Step 2 — Run tests (expect failure)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "disabled or name or tools or env_loading" -v
# Expected: ModuleNotFoundError — agents/browser_agent.py does not exist
```

- [ ] Confirm tests fail.

### Step 3 — Create `src/bridge/agents/browser_agent.py`

```python
"""BrowserAgent — headless browser automation via Playwright async API.

Every action passes through trust_engine before execution.
Sessions are ephemeral: one Playwright context per run(), destroyed on completion.
Domain allowlist enforced via BROWSER_ALLOWED_DOMAINS.
All actions are logged to SQLite browser_action_log.
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .base import AgentBase, AgentResult

logger = logging.getLogger("rag-bridge.agents.browser")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
BROWSER_DB_PATH = STATE_DIR / "browser.db"

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class NavigateResult:
    url: str
    title: str
    status_code: int
    duration_ms: int


@dataclass
class ScreenshotResult:
    b64_png: str
    url: str
    width: int
    height: int
    stored_qdrant: bool


@dataclass
class ExtractTextResult:
    text: str
    selector: str | None
    char_count: int
    truncated: bool


@dataclass
class ActionResult:
    success: bool
    action: str
    selector: str | None
    duration_ms: int


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class BrowserDisabledError(RuntimeError):
    """Raised when BROWSER_ENABLED=false."""


class BrowserDomainBlockedError(ValueError):
    """Raised when a URL's domain is not in the allowlist."""

    def __init__(self, url: str, hostname: str) -> None:
        self.url = url
        self.hostname = hostname
        super().__init__(f"Domain blocked: {hostname} (url={url})")


class BrowserInvalidURLError(ValueError):
    """Raised when the URL scheme is not http or https."""


class BrowserInvalidSelectorError(ValueError):
    """Raised when a fill() targets a non-fillable element."""


# ---------------------------------------------------------------------------
# BrowserAgent
# ---------------------------------------------------------------------------


class BrowserAgent(AgentBase):
    """Headless browser automation — Playwright async API."""

    name: str = "browser"
    description: str = (
        "Headless browser automation: navigate pages, extract text, "
        "fill forms, take screenshots — trust-gated per action type"
    )
    tools: list[str] = ["navigate", "screenshot", "extract_text", "click", "fill", "submit"]
    max_steps: int = 15

    # Trust overrides — browser_submit is never auto-promoted
    trust_overrides: dict[str, str] = {}

    def __init__(self, run_chat_fn, tool_registry=None, trust_engine=None) -> None:
        super().__init__(run_chat_fn, tool_registry, trust_engine)

        # Env loading
        self.browser_enabled: bool = os.getenv("BROWSER_ENABLED", "false").lower() == "true"
        raw_domains = os.getenv("BROWSER_ALLOWED_DOMAINS", "")
        self.allowed_domains: list[str] = (
            [d.strip() for d in raw_domains.split(",") if d.strip()]
            if raw_domains.strip() else []
        )
        self.page_timeout_ms: int = int(os.getenv("BROWSER_PAGE_TIMEOUT_MS", "30000"))
        self.max_session_s: int = int(os.getenv("BROWSER_MAX_SESSION_S", "300"))
        self.screenshot_store: bool = os.getenv("BROWSER_SCREENSHOT_STORE", "false").lower() == "true"
        self.playwright_browser: str = os.getenv("PLAYWRIGHT_BROWSER", "chromium")
        self.docker_sidecar: bool = os.getenv("BROWSER_DOCKER_SIDECAR", "false").lower() == "true"
        self.sidecar_ws_url: str = os.getenv("BROWSER_SIDECAR_WS_URL", "ws://browser:8765")

        if self.browser_enabled and not self.allowed_domains:
            logger.warning(
                "BROWSER_ALLOWED_DOMAINS est vide — tous les domaines sont autorisés. "
                "Définir BROWSER_ALLOWED_DOMAINS pour restreindre la navigation aux domaines de confiance."
            )

        # Internal session state (set during run())
        self._page: Any = None
        self._session_id: str = ""

    # ------------------------------------------------------------------
    # Public API — run()
    # ------------------------------------------------------------------

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Execute a browser task. Returns AgentResult(status='disabled') if BROWSER_ENABLED=false."""
        if not self.browser_enabled:
            return AgentResult(
                status="disabled",
                output="BrowserAgent is disabled. Set BROWSER_ENABLED=true to enable.",
            )

        self._session_id = str(uuid.uuid4())
        self._actions_log = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            return self._make_result(
                "failed",
                "playwright is not installed. Run: pip install playwright && playwright install chromium",
            )

        steps = self._parse_task(task)
        results: list[str] = []

        try:
            async with async_playwright() as pw:
                browser_instance = await self._launch_browser(pw)
                context_obj = await self._create_session(browser_instance)
                self._page = await context_obj.new_page()

                import asyncio  # noqa: PLC0415
                async with asyncio.timeout(self.max_session_s):
                    for step in steps:
                        step_result = await self._execute_step(step)
                        results.append(str(step_result))

                await context_obj.close()
                await browser_instance.close()

        except TimeoutError:
            return self._make_result(
                "failed",
                f"Session timeout after {self.max_session_s}s.",
            )
        except BrowserDomainBlockedError as exc:
            return self._make_result("failed", f"Domain blocked: {exc.hostname}")
        except Exception as exc:
            logger.exception("BrowserAgent.run() failed: %s", exc)
            return self._make_result("failed", f"Browser error: {exc}")
        finally:
            self._page = None

        output = "\n".join(results) if results else "No steps executed."
        return self._make_result("completed", output)

    # ------------------------------------------------------------------
    # Internal — task parsing (LLM call)
    # ------------------------------------------------------------------

    def _parse_task(self, task: str) -> list[dict[str, Any]]:
        """Ask LLM to decompose task into a list of browser action steps."""
        import json  # noqa: PLC0415
        try:
            result = self.run_chat_fn(
                "task_decomposition",
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a browser automation planner. "
                            "Given a task description, return a JSON array of steps. "
                            "Each step: {\"action\": \"navigate|screenshot|extract_text|click|fill|submit\", "
                            "\"url\": \"...\", \"selector\": \"...\", \"value\": \"...\"}. "
                            "Only include keys relevant to the action. Max 10 steps."
                        ),
                    },
                    {"role": "user", "content": task[:2000]},
                ],
                json_mode=True,
                max_tokens=500,
            )
            steps = json.loads(result["text"])
            if isinstance(steps, list):
                return steps[:10]
            return []
        except Exception as exc:
            logger.warning("BrowserAgent task parsing failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal — step dispatch
    # ------------------------------------------------------------------

    async def _execute_step(self, step: dict[str, Any]) -> Any:
        action = step.get("action", "")
        if action == "navigate":
            return await self.navigate(step["url"])
        if action == "screenshot":
            return await self.screenshot()
        if action == "extract_text":
            return await self.extract_text(step.get("selector"))
        if action == "click":
            return await self.click(step["selector"])
        if action == "fill":
            return await self.fill(step["selector"], step.get("value", ""))
        if action == "submit":
            return await self.submit(step.get("selector"))
        logger.warning("Unknown browser action: %s", action)
        return None

    # ------------------------------------------------------------------
    # Browser launch helpers
    # ------------------------------------------------------------------

    async def _launch_browser(self, pw: Any) -> Any:
        if self.docker_sidecar:
            return await pw.chromium.connect(self.sidecar_ws_url)
        return await pw.chromium.launch(headless=True)

    async def _create_session(self, browser: Any) -> Any:
        """Create an isolated browser context with security settings."""
        return await browser.new_context(
            accept_downloads=False,
            geolocation=None,
            permissions=[],  # no camera, mic, notifications
            java_script_enabled=True,
            viewport={"width": 1280, "height": 800},
        )

    # ------------------------------------------------------------------
    # Action methods
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> NavigateResult:
        """Navigate to url. Validates scheme and allowlist first."""
        self._validate_url_scheme(url)
        self._check_domain_allowlist(url)
        t0 = time.monotonic()
        response = await self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.page_timeout_ms,
        )
        title = await self._page.title()
        status_code = response.status if response else 0
        duration_ms = int((time.monotonic() - t0) * 1000)
        current_url = self._page.url
        self._log_browser_action("navigate", current_url, None, "ok", "auto", "auto", duration_ms)
        self._log_action("navigate", {"url": url}, f"status={status_code} title={title!r}")
        return NavigateResult(
            url=current_url,
            title=title,
            status_code=status_code,
            duration_ms=duration_ms,
        )

    async def extract_text(self, selector: str | None = None) -> ExtractTextResult:
        """Extract plain text from the page or a specific element. Never returns HTML."""
        t0 = time.monotonic()
        if selector:
            element = await self._page.query_selector(selector)
            text = (await element.inner_text()) if element else ""
        else:
            text = await self._page.inner_text("body")
        truncated = len(text) > 50_000
        if truncated:
            text = text[:50_000]
        duration_ms = int((time.monotonic() - t0) * 1000)
        current_url = self._page.url
        self._log_browser_action("extract_text", current_url, selector, "ok", "auto", "auto", duration_ms)
        self._log_action("extract_text", {"selector": selector}, f"chars={len(text)}")
        return ExtractTextResult(
            text=text,
            selector=selector,
            char_count=len(text),
            truncated=truncated,
        )

    async def click(self, selector: str) -> ActionResult:
        """Click an element. Trust level: browser_read (non-submit) or browser_submit (submit buttons)."""
        trust_action = self._get_trust_action_type_for_click(selector)
        current_url = self._page.url
        trust_result = self._execute_with_trust(
            trust_action,
            f"click selector={selector!r} url={current_url}",
        )
        if trust_result.get("status") in ("approval_required", "pending_notify"):
            self._log_browser_action(
                "click", current_url, selector, "pending",
                trust_result.get("trust_level", trust_action), None, 0,
            )
            return ActionResult(success=False, action="click", selector=selector, duration_ms=0)

        t0 = time.monotonic()
        await self._page.click(selector)
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_browser_action("click", current_url, selector, "ok", trust_action, "auto", duration_ms)
        self._log_action("click", {"selector": selector}, "ok")
        return ActionResult(success=True, action="click", selector=selector, duration_ms=duration_ms)

    async def fill(self, selector: str, value: str) -> ActionResult:
        """Fill a form field. Trust level: browser_fill. Value is masked in logs."""
        current_url = self._page.url
        trust_result = self._execute_with_trust(
            "browser_fill",
            f"fill selector={selector!r} url={current_url}",
        )
        if trust_result.get("status") in ("approval_required", "pending_notify"):
            self._log_browser_action(
                "fill", current_url, selector, "pending", "browser_fill", None, 0,
            )
            return ActionResult(success=False, action="fill", selector=selector, duration_ms=0)

        t0 = time.monotonic()
        element = await self._page.query_selector(selector)
        if element:
            tag = (await element.get_attribute("tagName") or "").lower()
            el_type = (await element.get_attribute("type") or "").lower()
            if tag not in ("input", "textarea") and el_type not in ("text", "email", "password", "search", "tel", "url", "number"):
                raise BrowserInvalidSelectorError(
                    f"Selector {selector!r} targets a non-fillable element (tag={tag!r})"
                )
        await self._page.fill(selector, value)
        duration_ms = int((time.monotonic() - t0) * 1000)
        # Mask value in all logs
        self._log_browser_action("fill", current_url, selector, "ok", "browser_fill", "auto", duration_ms)
        self._log_action("fill", {"selector": selector, "value": "***"}, "ok")
        return ActionResult(success=True, action="fill", selector=selector, duration_ms=duration_ms)

    async def submit(self, selector: str | None = None) -> ActionResult:
        """Submit a form. Trust level: browser_submit — NEVER auto-promoted."""
        current_url = self._page.url
        trust_result = self._execute_with_trust(
            "browser_submit",
            f"submit selector={selector!r} url={current_url}",
        )
        if trust_result.get("status") in ("approval_required", "pending_notify"):
            self._log_browser_action(
                "submit", current_url, selector, "pending", "browser_submit", None, 0,
            )
            return ActionResult(success=False, action="submit", selector=selector, duration_ms=0)

        t0 = time.monotonic()
        if selector:
            await self._page.click(selector)
        else:
            # Fallback: find a submit button
            for fallback in ('[type="submit"]', 'button[type="submit"]', 'input[type="submit"]'):
                el = await self._page.query_selector(fallback)
                if el:
                    await self._page.click(fallback)
                    break
        await self._page.wait_for_load_state("networkidle", timeout=10_000)
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_browser_action("submit", current_url, selector, "ok", "browser_submit", "admin", duration_ms)
        self._log_action("submit", {"selector": selector}, "ok")
        return ActionResult(success=True, action="submit", selector=selector, duration_ms=duration_ms)

    async def screenshot(self) -> ScreenshotResult:
        """Capture a PNG screenshot. Optionally upserts to Qdrant if BROWSER_SCREENSHOT_STORE=true."""
        t0 = time.monotonic()
        png_bytes = await self._page.screenshot(type="png", full_page=False)
        b64_png = base64.b64encode(png_bytes).decode("utf-8")
        viewport = self._page.viewport_size or {"width": 1280, "height": 800}
        current_url = self._page.url
        stored = False
        if self.screenshot_store:
            try:
                await self._store_screenshot_qdrant(b64_png, current_url, "screenshot")
                stored = True
            except Exception as exc:
                logger.warning("Screenshot Qdrant store failed: %s", exc)
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_browser_action("screenshot", current_url, None, "ok", "auto", "auto", duration_ms)
        self._log_action("screenshot", {"url": current_url}, f"stored_qdrant={stored}")
        return ScreenshotResult(
            b64_png=b64_png,
            url=current_url,
            width=viewport["width"],
            height=viewport["height"],
            stored_qdrant=stored,
        )

    # ------------------------------------------------------------------
    # Domain allowlist
    # ------------------------------------------------------------------

    def _validate_url_scheme(self, url: str) -> None:
        """Reject non-http(s) schemes."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise BrowserInvalidURLError(
                f"URL scheme {parsed.scheme!r} is not allowed. Only http and https are permitted."
            )

    def _check_domain_allowlist(self, url: str) -> bool:
        """Returns True if the domain is allowed, raises BrowserDomainBlockedError otherwise."""
        if not self.allowed_domains:
            logger.warning("Allowlist is empty — all domains allowed for url=%s", url)
            return True
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Normalise: strip leading www.
        if hostname.startswith("www."):
            hostname = hostname[4:]
        for domain in self.allowed_domains:
            d = domain.lstrip("www.") if domain.startswith("www.") else domain
            if hostname == d or hostname.endswith("." + d):
                return True
        raise BrowserDomainBlockedError(url, hostname)

    # ------------------------------------------------------------------
    # Trust helpers
    # ------------------------------------------------------------------

    def _get_trust_action_type(self, action: str) -> str:
        """Map a browser action name to its trust action type."""
        mapping = {
            "navigate": "browser_read",
            "screenshot": "browser_read",
            "extract_text": "browser_read",
            "wait_for": "browser_read",
            "click": "browser_read",
            "fill": "browser_fill",
            "submit": "browser_submit",
        }
        return mapping.get(action, "browser_read")

    def _get_trust_action_type_for_click(self, selector: str) -> str:
        """Reclassify click as browser_submit if the selector targets a submit button."""
        selector_lower = selector.lower()
        submit_indicators = ['[type="submit"]', "[type='submit']", "type=submit", 'button[type="submit"]']
        if any(ind in selector_lower for ind in submit_indicators):
            return "browser_submit"
        return "browser_read"

    def _execute_with_trust(self, action_type: str, action_detail: str) -> dict[str, Any]:
        """Gate an action through the trust engine if available."""
        if self.trust_engine is None:
            return {"ok": True, "status": "executed"}
        import src.bridge.trust_engine as te  # noqa: PLC0415
        return te.check_and_execute(
            action_type,
            action_detail,
            action_fn=lambda: None,  # action is executed by caller
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_browser_action(
        self,
        action_type: str,
        url: str,
        selector: str | None,
        status: str,
        trust_level: str,
        approved_by: str | None,
        duration_ms: int,
        error_msg: str | None = None,
    ) -> None:
        """Persist action to browser_action_log SQLite table."""
        from datetime import datetime, timezone  # noqa: PLC0415
        now = datetime.now(timezone.utc).isoformat()
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(BROWSER_DB_PATH))
            db.execute(
                "INSERT OR IGNORE INTO browser_action_log "
                "(id, session_id, action_type, url, selector, status, "
                "trust_level, approved_by, started_at, duration_ms, error_msg) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    self._session_id,
                    action_type,
                    url,
                    selector,
                    status,
                    trust_level,
                    approved_by,
                    now,
                    duration_ms,
                    error_msg,
                ),
            )
            db.commit()
        except Exception as exc:
            logger.warning("browser_action_log insert failed: %s", exc)
        finally:
            try:
                db.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Qdrant screenshot store
    # ------------------------------------------------------------------

    async def _store_screenshot_qdrant(
        self, b64_png: str, url: str, action_context: str
    ) -> None:
        """Upsert screenshot to Qdrant browser_screenshots collection (TTL 24h)."""
        import uuid as _uuid  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415
        from qdrant_client import QdrantClient, models  # noqa: PLC0415

        title = ""
        if self._page:
            title = await self._page.title()

        now = datetime.now(timezone.utc).isoformat()
        point_id = str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            f"{self._session_id}_{url}_{now}",
        ))
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        client = QdrantClient(url=qdrant_url)
        client.upsert(
            collection_name="browser_screenshots",
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=[0.0] * 384,  # placeholder — embed url+title+context in production
                    payload={
                        "b64_png": b64_png,
                        "url": url,
                        "page_title": title,
                        "action_context": action_context,
                        "session_id": self._session_id,
                        "width": (self._page.viewport_size or {}).get("width", 1280),
                        "height": (self._page.viewport_size or {}).get("height", 800),
                        "source": "browser_screenshot",
                        "created_at": now,
                    },
                )
            ],
        )
        logger.debug("Screenshot stored in Qdrant: point_id=%s url=%s", point_id, url)
```

- [ ] Create `src/bridge/agents/browser_agent.py` with the content above.

### Step 4 — Run tests (expect green)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "disabled or name or tools or env_loading" -v
# Expected: 5 passed
```

- [ ] All 5 skeleton tests pass.

### Step 5 — Commit

```bash
git add src/bridge/agents/browser_agent.py tests/test_browser_agent.py
git commit -m "feat(browser): add BrowserAgent skeleton — BROWSER_ENABLED guard, env loading, result dataclasses"
```

- [ ] Commit created.

---

## Task 4 — `_check_domain_allowlist(url)`

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
def test_allowlist_empty_allows_all(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    # Should return True without raising
    assert agent._check_domain_allowlist("https://anything.example.com") is True


def test_allowlist_blocks_unlisted_domain(monkeypatch):
    from agents.browser_agent import BrowserDomainBlockedError
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    with pytest.raises(BrowserDomainBlockedError):
        agent._check_domain_allowlist("https://evil.com/steal")


def test_allowlist_allows_listed_domain(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com,docs.python.org")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent._check_domain_allowlist("https://github.com/torvalds/linux") is True
    assert agent._check_domain_allowlist("https://docs.python.org/3/") is True


def test_allowlist_allows_subdomains(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent._check_domain_allowlist("https://gist.github.com/user/abc") is True
    assert agent._check_domain_allowlist("https://api.github.com/repos") is True


def test_allowlist_strips_www(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent._check_domain_allowlist("https://www.github.com/") is True


def test_invalid_scheme_raises(monkeypatch):
    from agents.browser_agent import BrowserInvalidURLError
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    with pytest.raises(BrowserInvalidURLError):
        agent._validate_url_scheme("file:///etc/passwd")
    with pytest.raises(BrowserInvalidURLError):
        agent._validate_url_scheme("javascript:alert(1)")
    with pytest.raises(BrowserInvalidURLError):
        agent._validate_url_scheme("data:text/html,<h1>xss</h1>")
```

- [ ] Add the 6 allowlist/scheme tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests (expect green — implementation already in skeleton)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "allowlist or subdomain or www or scheme" -v
# Expected: 6 passed
```

- [ ] All 6 allowlist tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add _check_domain_allowlist and URL scheme validation tests"
```

- [ ] Commit created.

---

## Task 5 — `navigate(url)` with Playwright mock

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_page(url="https://example.com", title="Example", status=200):
    """Build a minimal mock Playwright Page."""
    page = MagicMock()
    page.url = url
    page.viewport_size = {"width": 1280, "height": 800}
    page.goto = AsyncMock(return_value=MagicMock(status=status))
    page.title = AsyncMock(return_value=title)
    page.inner_text = AsyncMock(return_value="Hello world")
    page.query_selector = AsyncMock(return_value=None)
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_navigate_returns_navigate_result(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import NavigateResult
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._page = _make_mock_page()
    agent._session_id = "test-session"

    result = await agent.navigate("https://example.com")
    assert isinstance(result, NavigateResult)
    assert result.status_code == 200
    assert result.title == "Example"
    assert result.url == "https://example.com"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_navigate_blocked_domain(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "github.com")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import BrowserDomainBlockedError
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._page = _make_mock_page()
    agent._session_id = "test-session"

    with pytest.raises(BrowserDomainBlockedError):
        await agent.navigate("https://evil.com")


@pytest.mark.asyncio
async def test_navigate_calls_goto_with_domcontentloaded(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    await agent.navigate("https://example.com")
    page.goto.assert_called_once()
    call_kwargs = page.goto.call_args[1]
    assert call_kwargs.get("wait_until") == "domcontentloaded"
```

- [ ] Add the 3 navigate tests to `tests/test_browser_agent.py`.
- [ ] Add `pytest-asyncio` to `src/bridge/requirements.txt` (test dependency) if not present.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && pip install pytest-asyncio
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "navigate" -v
# Expected: 3 passed
```

- [ ] All 3 navigate tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add navigate() tests with Playwright mock"
```

- [ ] Commit created.

---

## Task 6 — `extract_text(selector)`

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_extract_text_full_body(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import ExtractTextResult
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    page.inner_text = AsyncMock(return_value="Hello world from body")
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.extract_text()
    assert isinstance(result, ExtractTextResult)
    assert result.text == "Hello world from body"
    assert result.selector is None
    assert result.char_count == len("Hello world from body")
    assert result.truncated is False
    # Must use inner_text, never inner_html
    page.inner_text.assert_called_once_with("body")


@pytest.mark.asyncio
async def test_extract_text_with_selector(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    mock_element = MagicMock()
    mock_element.inner_text = AsyncMock(return_value="Specific section text")
    page.query_selector = AsyncMock(return_value=mock_element)
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.extract_text("#main-content")
    assert result.text == "Specific section text"
    assert result.selector == "#main-content"


@pytest.mark.asyncio
async def test_extract_text_selector_not_found(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    page.query_selector = AsyncMock(return_value=None)
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.extract_text(".nonexistent")
    assert result.text == ""
    assert result.truncated is False


@pytest.mark.asyncio
async def test_extract_text_truncates_at_50000(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    long_text = "A" * 60_000
    page.inner_text = AsyncMock(return_value=long_text)
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.extract_text()
    assert len(result.text) == 50_000
    assert result.truncated is True
```

- [ ] Add the 4 extract_text tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "extract_text" -v
# Expected: 4 passed
```

- [ ] All 4 extract_text tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add extract_text() tests — inner_text, truncation, selector"
```

- [ ] Commit created.

---

## Task 7 — `click(selector)` with trust check

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_click_succeeds_when_trust_engine_none(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import ActionResult
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.click("#some-button")
    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.action == "click"
    page.click.assert_called_once_with("#some-button")


@pytest.mark.asyncio
async def test_click_submit_button_reclassified(monkeypatch):
    """Click on [type=submit] must be classified as browser_submit."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent._get_trust_action_type_for_click('[type="submit"]') == "browser_submit"
    assert agent._get_trust_action_type_for_click("#login-btn") == "browser_read"


@pytest.mark.asyncio
async def test_click_blocked_by_trust_engine(monkeypatch):
    """When trust engine returns approval_required, click returns success=False."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import ActionResult
    mock_te = MagicMock()
    mock_te.check_and_execute = MagicMock(return_value={
        "ok": True,
        "status": "approval_required",
        "action_type": "browser_read",
    })
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=mock_te)
    # Patch _execute_with_trust directly
    agent._execute_with_trust = MagicMock(return_value={
        "ok": True, "status": "approval_required"
    })
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.click("#submit")
    assert result.success is False
    page.click.assert_not_called()
```

- [ ] Add the 3 click tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "click" -v
# Expected: 3 passed
```

- [ ] All 3 click tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add click() tests — trust check, submit reclassification"
```

- [ ] Commit created.

---

## Task 8 — `fill(selector, value)` with value masking

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_fill_calls_playwright_fill(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    mock_element = MagicMock()
    mock_element.get_attribute = AsyncMock(side_effect=lambda attr: "input" if attr == "tagName" else "text")
    page.query_selector = AsyncMock(return_value=mock_element)
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.fill("#email", "user@example.com")
    assert result.success is True
    page.fill.assert_called_once_with("#email", "user@example.com")


@pytest.mark.asyncio
async def test_fill_masks_value_in_actions_log(monkeypatch):
    """The value must appear as '***' in _actions_log — never plain text."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    mock_element = MagicMock()
    mock_element.get_attribute = AsyncMock(side_effect=lambda attr: "input" if attr == "tagName" else "text")
    page.query_selector = AsyncMock(return_value=mock_element)
    agent._page = page
    agent._session_id = "test-session"

    await agent.fill("#password", "super_secret_123")
    # Check _actions_log — value must be masked
    fill_actions = [a for a in agent._actions_log if a["action"] == "fill"]
    assert fill_actions, "No fill action in _actions_log"
    assert fill_actions[0]["params"].get("value") == "***"
    assert "super_secret_123" not in str(fill_actions[0])


@pytest.mark.asyncio
async def test_fill_blocked_when_trust_approval_required(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._execute_with_trust = MagicMock(return_value={
        "ok": True, "status": "approval_required"
    })
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.fill("#email", "test@test.com")
    assert result.success is False
    page.fill.assert_not_called()
```

- [ ] Add the 3 fill tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "fill" -v
# Expected: 3 passed
```

- [ ] All 3 fill tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add fill() tests — value masking, trust gate, playwright call"
```

- [ ] Commit created.

---

## Task 9 — `submit(selector)` — approval_required, never auto-promoted

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_submit_always_requires_approval_by_default(monkeypatch):
    """submit() must return success=False when trust engine returns approval_required."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._execute_with_trust = MagicMock(return_value={
        "ok": True, "status": "approval_required", "action_type": "browser_submit"
    })
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.submit("#submit-btn")
    assert result.success is False
    page.click.assert_not_called()


@pytest.mark.asyncio
async def test_submit_executes_when_trust_is_auto(monkeypatch):
    """When trust engine is bypassed (None), submit executes the click."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.submit("#submit-btn")
    assert result.success is True
    page.click.assert_called_once_with("#submit-btn")
    page.wait_for_load_state.assert_called_once_with("networkidle", timeout=10_000)


@pytest.mark.asyncio
async def test_submit_fallback_selector(monkeypatch):
    """submit(None) must search for [type=submit] fallback."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    # First fallback found
    page.query_selector = AsyncMock(return_value=MagicMock())
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.submit(None)
    assert result.success is True
    page.click.assert_called_once()
```

- [ ] Add the 3 submit tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "submit" -v
# Expected: 3 passed
```

- [ ] All 3 submit tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add submit() tests — approval gate, fallback selector"
```

- [ ] Commit created.

---

## Task 10 — `screenshot()` + optional Qdrant store

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_screenshot_returns_base64(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    monkeypatch.setenv("BROWSER_SCREENSHOT_STORE", "false")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import ScreenshotResult
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.screenshot()
    assert isinstance(result, ScreenshotResult)
    assert isinstance(result.b64_png, str)
    assert len(result.b64_png) > 0
    assert result.stored_qdrant is False
    assert result.width == 1280
    assert result.height == 800


@pytest.mark.asyncio
async def test_screenshot_store_false_does_not_call_qdrant(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    monkeypatch.setenv("BROWSER_SCREENSHOT_STORE", "false")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._store_screenshot_qdrant = AsyncMock()
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    await agent.screenshot()
    agent._store_screenshot_qdrant.assert_not_called()


@pytest.mark.asyncio
async def test_screenshot_store_true_calls_qdrant(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    monkeypatch.setenv("BROWSER_SCREENSHOT_STORE", "true")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._store_screenshot_qdrant = AsyncMock()
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.screenshot()
    agent._store_screenshot_qdrant.assert_called_once()
    assert result.stored_qdrant is True


@pytest.mark.asyncio
async def test_screenshot_store_failure_does_not_raise(monkeypatch):
    """Qdrant failure must be swallowed — screenshot still succeeds."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    monkeypatch.setenv("BROWSER_SCREENSHOT_STORE", "true")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    agent._store_screenshot_qdrant = AsyncMock(side_effect=Exception("Qdrant down"))
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"

    result = await agent.screenshot()
    # Must not raise, stored_qdrant must be False on failure
    assert result.stored_qdrant is False
```

- [ ] Add the 4 screenshot tests to `tests/test_browser_agent.py`.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "screenshot" -v
# Expected: 4 passed
```

- [ ] All 4 screenshot tests pass.

### Step 3 — Commit

```bash
git add tests/test_browser_agent.py
git commit -m "test(browser): add screenshot() tests — base64, Qdrant store toggle, failure isolation"
```

- [ ] Commit created.

---

## Task 11 — `BrowserAgent.run(task)` — full pipeline

### Step 1 — Write the tests

Add to `tests/test_browser_agent.py`:

```python
@pytest.mark.asyncio
async def test_run_returns_disabled_when_env_false(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "false")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    result = await agent.run("navigate to example.com")
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_run_returns_failed_when_playwright_not_installed(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    # Simulate playwright not installed
    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        result = await agent.run("navigate to example.com")
    assert result.status == "failed"
    assert "playwright" in result.output.lower()


@pytest.mark.asyncio
async def test_run_executes_navigate_step(monkeypatch):
    """Full pipeline: parse_task returns [navigate], run() executes it."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import NavigateResult, AgentResult as _AR
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": '[{"action": "navigate", "url": "https://example.com"}]'})

    mock_page = _make_mock_page()

    # Mock the entire playwright context manager chain
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    mock_pw = AsyncMock()
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)
    mock_pw.chromium = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    with patch("agents.browser_agent.async_playwright", return_value=mock_pw):
        result = await agent.run("navigate to example.com")

    assert result.status == "completed"
    assert len(result.actions_taken) > 0


@pytest.mark.asyncio
async def test_run_records_actions_in_actions_taken(monkeypatch):
    """actions_taken in AgentResult must include entries from the run."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(
        run_chat_fn=lambda *a, **k: {
            "text": '[{"action": "navigate", "url": "https://example.com"}, {"action": "screenshot"}]'
        }
    )
    mock_page = _make_mock_page()
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_pw = AsyncMock()
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)
    mock_pw.chromium = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    agent._store_screenshot_qdrant = AsyncMock()

    with patch("agents.browser_agent.async_playwright", return_value=mock_pw):
        result = await agent.run("navigate and screenshot")

    assert result.status == "completed"
    action_names = [a["action"] for a in result.actions_taken]
    assert "navigate" in action_names
    assert "screenshot" in action_names
```

- [ ] Add the 4 run() pipeline tests to `tests/test_browser_agent.py`.

**Note:** The `run()` method imports `async_playwright` inside the function body. Adjust the import target for the `patch()` call to match the module path: `agents.browser_agent.async_playwright`. This requires moving the `from playwright.async_api import async_playwright` import to module level with a try/except guard:

Update `src/bridge/agents/browser_agent.py` — move the playwright import to module level:

```python
# At the top of browser_agent.py, after standard imports:
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False
```

Then in `run()`, check `PLAYWRIGHT_AVAILABLE` instead of the inline import.

- [ ] Update `src/bridge/agents/browser_agent.py` to use module-level playwright import with try/except guard.

### Step 2 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "run_" -v
# Expected: 4 passed
```

- [ ] All 4 run() tests pass.

### Step 3 — Commit

```bash
git add src/bridge/agents/browser_agent.py tests/test_browser_agent.py
git commit -m "feat(browser): implement BrowserAgent.run() full pipeline + tests"
```

- [ ] Commit created.

---

## Task 12 — Register in AGENT_REGISTRY + default trust policies

### Step 1 — Write the test

Add to `tests/test_browser_agent.py`:

```python
def test_browser_agent_registered_in_registry(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    # Clear registry import cache
    for key in list(sys.modules.keys()):
        if "agents" in key and "browser" not in key:
            pass  # keep agents/__init__ to test registration
    import importlib
    import agents as agents_pkg  # noqa: PLC0415
    importlib.reload(agents_pkg)
    from agents import AGENT_REGISTRY  # noqa: PLC0415
    assert "browser" in AGENT_REGISTRY


def test_browser_agent_default_trust_policies():
    """browser_read, browser_fill, browser_submit must have seeded default policies."""
    # This test seeds policies and verifies they are set correctly.
    import sys
    sys.path.insert(0, str(
        __import__("pathlib").Path(__file__).parent.parent / "src" / "bridge"
    ))
    import trust_engine  # noqa: PLC0415
    # browser_submit must never auto-promote
    from agents.browser_agent import seed_default_trust_policies  # noqa: PLC0415
    import tempfile, os  # noqa: PLC0415, E401
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch_env = {"RAG_STATE_DIR": tmp, "TRUST_ENGINE_ENABLED": "true"}
        original = {k: os.environ.get(k) for k in monkeypatch_env}
        os.environ.update(monkeypatch_env)
        try:
            # Reload trust_engine with new STATE_DIR
            importlib.reload(trust_engine)
            seed_default_trust_policies()
            submit_level = trust_engine.get_trust_level("browser_submit")
            read_level = trust_engine.get_trust_level("browser_read")
            fill_level = trust_engine.get_trust_level("browser_fill")
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    assert submit_level == "approval_required"
    assert read_level == "notify_then_execute"
    assert fill_level == "approval_required"
```

- [ ] Add the 2 registry tests to `tests/test_browser_agent.py`.

### Step 2 — Update `src/bridge/agents/__init__.py`

Add the `browser` agent registration block to `_register_defaults()`:

```python
    try:
        from .browser_agent import BrowserAgent  # noqa: WPS433

        register_agent("browser", BrowserAgent)
    except ImportError:
        pass
```

- [ ] Add the browser agent registration block to `src/bridge/agents/__init__.py` inside `_register_defaults()`.

### Step 3 — Add `seed_default_trust_policies()` to `browser_agent.py`

Add this function at the module level (after the class definition):

```python
def seed_default_trust_policies() -> None:
    """Seed browser_* trust policies into the trust engine.

    Called once at BrowserAgent init if trust_engine is available.
    browser_submit is NEVER auto-promoted (auto_promote_after=0).
    """
    try:
        import trust_engine as te  # noqa: PLC0415
    except ImportError:
        logger.warning("trust_engine not available — skipping browser policy seeding")
        return

    defaults = [
        # action_type           level                   auto_promote_after
        ("browser_read",    "notify_then_execute",  20),
        ("browser_fill",    "approval_required",    20),
        ("browser_submit",  "approval_required",     0),  # NEVER auto-promoted
    ]
    for action_type, level, promote_after in defaults:
        existing = te.get_trust_level(action_type)
        # Only seed if no existing policy (default fallback value means no explicit row)
        if existing == te.TRUST_DEFAULT_LEVEL:
            te.set_trust_level(action_type, level)
            # Set auto_promote_after directly in DB if needed
            if promote_after != te.TRUST_AUTO_PROMOTE_THRESHOLD:
                import sqlite3 as _sq  # noqa: PLC0415
                from datetime import datetime, timezone  # noqa: PLC0415
                now = datetime.now(timezone.utc).isoformat()
                db = _sq.connect(str(te.TRUST_DB_PATH))
                try:
                    db.execute(
                        "UPDATE trust_policies SET auto_promote_after = ? WHERE action_type = ?",
                        (promote_after, action_type),
                    )
                    db.commit()
                finally:
                    db.close()
            logger.debug("Seeded trust policy: %s -> %s (auto_promote_after=%d)", action_type, level, promote_after)
```

Also call `seed_default_trust_policies()` inside `BrowserAgent.__init__` when `self.browser_enabled is True` and `trust_engine is not None`.

- [ ] Add `seed_default_trust_policies()` to `src/bridge/agents/browser_agent.py`.
- [ ] Call `seed_default_trust_policies()` in `BrowserAgent.__init__` when enabled.

### Step 4 — Run tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_agent.py -k "registry or trust_policies" -v
# Expected: 2 passed
```

- [ ] Both registry/policy tests pass.

### Step 5 — Commit

```bash
git add src/bridge/agents/__init__.py src/bridge/agents/browser_agent.py tests/test_browser_agent.py
git commit -m "feat(browser): register BrowserAgent in AGENT_REGISTRY + seed default trust policies"
```

- [ ] Commit created.

---

## Task 13 — `src/bridge/browser_api.py`

### Step 1 — Write the tests

Create `tests/test_browser_api.py`:

```python
"""Tests for browser API endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))


def _make_test_app():
    """Create a minimal FastAPI app with the browser router mounted."""
    from fastapi import FastAPI  # noqa: PLC0415
    import importlib  # noqa: PLC0415
    if "browser_api" in sys.modules:
        del sys.modules["browser_api"]
    browser_api = importlib.import_module("browser_api")
    app = FastAPI()
    app.include_router(browser_api.router)
    return app, browser_api


def test_browser_run_endpoint_disabled(monkeypatch):
    """POST /api/browser/run returns 503 when BROWSER_ENABLED=false."""
    monkeypatch.setenv("BROWSER_ENABLED", "false")
    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.post(
        "/api/browser/run",
        json={"task": "navigate to example.com"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code in (200, 503)
    body = response.json()
    # If 200, status must be 'disabled'
    if response.status_code == 200:
        assert body.get("status") == "disabled"


def test_browser_run_endpoint_returns_result(monkeypatch):
    """POST /api/browser/run returns AgentResult-shaped JSON."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    app, browser_api = _make_test_app()

    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.output = "Navigated to example.com"
    mock_result.actions_taken = []
    mock_result.cost_tokens = 0
    mock_result.artifacts = {}

    async def mock_run(task, context=None):
        return mock_result

    mock_agent = MagicMock()
    mock_agent.run = mock_run

    with patch.object(browser_api, "_get_browser_agent", return_value=mock_agent):
        client = TestClient(app)
        response = client.post(
            "/api/browser/run",
            json={"task": "navigate to example.com"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"


def test_browser_action_log_endpoint(monkeypatch, tmp_path):
    """GET /api/browser/action-log returns list of log entries."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    # Seed a log entry
    import sqlite3  # noqa: PLC0415
    db_path = tmp_path / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""CREATE TABLE browser_action_log (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action_type TEXT NOT NULL,
        url TEXT NOT NULL, selector TEXT, status TEXT NOT NULL, trust_level TEXT NOT NULL,
        approved_by TEXT, started_at TEXT NOT NULL, duration_ms INTEGER, error_msg TEXT
    )""")
    db.execute(
        "INSERT INTO browser_action_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("id-1", "sess-1", "navigate", "https://example.com", None, "ok", "auto", "auto",
         "2026-01-01T00:00:00Z", 100, None),
    )
    db.commit()
    db.close()

    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.get("/api/browser/action-log")
    assert response.status_code == 200
    body = response.json()
    assert "entries" in body
    assert len(body["entries"]) >= 1


def test_browser_sessions_endpoint(monkeypatch, tmp_path):
    """GET /api/browser/sessions returns session summaries."""
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    import sqlite3  # noqa: PLC0415
    db_path = tmp_path / "browser.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""CREATE TABLE browser_action_log (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action_type TEXT NOT NULL,
        url TEXT NOT NULL, selector TEXT, status TEXT NOT NULL, trust_level TEXT NOT NULL,
        approved_by TEXT, started_at TEXT NOT NULL, duration_ms INTEGER, error_msg TEXT
    )""")
    db.execute(
        "INSERT INTO browser_action_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("id-1", "sess-abc", "navigate", "https://example.com", None, "ok", "auto", "auto",
         "2026-01-01T00:00:00Z", 100, None),
    )
    db.commit()
    db.close()

    app, _ = _make_test_app()
    client = TestClient(app)
    response = client.get("/api/browser/sessions")
    assert response.status_code == 200
    body = response.json()
    assert "sessions" in body
```

- [ ] Create `tests/test_browser_api.py` with the content above.

### Step 2 — Run tests (expect failure — module missing)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_api.py -v
# Expected: ModuleNotFoundError — browser_api does not exist
```

- [ ] Confirm tests fail.

### Step 3 — Create `src/bridge/browser_api.py`

```python
"""Browser automation API endpoints.

POST /api/browser/run        — run a browser task via BrowserAgent
GET  /api/browser/sessions   — list recent sessions from browser_action_log
GET  /api/browser/action-log — raw action log with optional filters
"""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.browser-api")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
BROWSER_DB_PATH = STATE_DIR / "browser.db"
BROWSER_ENABLED = os.getenv("BROWSER_ENABLED", "false").lower() == "true"

router = APIRouter(prefix="/api/browser", tags=["browser"])

_browser_agent_instance: Any = None
_verify_token = None


def init_browser_api(browser_agent=None, verify_token_dep=None) -> None:
    """Called from app.py startup to inject agent and auth dependency."""
    global _browser_agent_instance, _verify_token
    _browser_agent_instance = browser_agent
    _verify_token = verify_token_dep


def _get_browser_agent() -> Any:
    return _browser_agent_instance


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class BrowserRunRequest(BaseModel):
    task: str
    context: dict[str, Any] | None = None


class BrowserRunResponse(BaseModel):
    status: str
    output: str
    actions_taken: list[dict[str, Any]] = []
    cost_tokens: int = 0
    artifacts: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action_log_row_to_dict(row: tuple) -> dict[str, Any]:
    keys = [
        "id", "session_id", "action_type", "url", "selector",
        "status", "trust_level", "approved_by", "started_at",
        "duration_ms", "error_msg",
    ]
    return dict(zip(keys, row))


def _get_action_log(limit: int = 100, session_id: str | None = None) -> list[dict[str, Any]]:
    if not BROWSER_DB_PATH.exists():
        return []
    db = sqlite3.connect(str(BROWSER_DB_PATH))
    try:
        if session_id:
            rows = db.execute(
                "SELECT * FROM browser_action_log WHERE session_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM browser_action_log ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_action_log_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()


def _get_sessions(limit: int = 50) -> list[dict[str, Any]]:
    if not BROWSER_DB_PATH.exists():
        return []
    db = sqlite3.connect(str(BROWSER_DB_PATH))
    try:
        rows = db.execute(
            "SELECT session_id, MIN(started_at) as started_at, "
            "COUNT(*) as action_count, MAX(url) as last_url, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_count "
            "FROM browser_action_log "
            "GROUP BY session_id "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "session_id": r[0],
                "started_at": r[1],
                "action_count": r[2],
                "last_url": r[3],
                "error_count": r[4],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=BrowserRunResponse)
async def browser_run(body: BrowserRunRequest, request: Request):
    """Run a browser task via BrowserAgent."""
    if _verify_token:
        _verify_token(request)

    if not BROWSER_ENABLED:
        return BrowserRunResponse(
            status="disabled",
            output="BrowserAgent is disabled. Set BROWSER_ENABLED=true to enable.",
        )

    agent = _get_browser_agent()
    if agent is None:
        raise HTTPException(status_code=503, detail="BrowserAgent not initialised")

    try:
        result = await agent.run(body.task, body.context)
        return BrowserRunResponse(
            status=result.status,
            output=result.output,
            actions_taken=result.actions_taken,
            cost_tokens=result.cost_tokens,
            artifacts=result.artifacts,
        )
    except Exception as exc:
        logger.exception("browser_run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sessions")
def browser_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
):
    """List recent browser sessions."""
    if _verify_token:
        _verify_token(request)
    return {"sessions": _get_sessions(limit=limit)}


@router.get("/action-log")
def browser_action_log(
    request: Request,
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get browser action log entries, optionally filtered by session_id."""
    if _verify_token:
        _verify_token(request)
    return {"entries": _get_action_log(limit=limit, session_id=session_id)}
```

- [ ] Create `src/bridge/browser_api.py` with the content above.

### Step 4 — Run tests (expect green)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_api.py -v
# Expected: 4 passed
```

- [ ] All 4 browser API tests pass.

### Step 5 — Commit

```bash
git add src/bridge/browser_api.py tests/test_browser_api.py
git commit -m "feat(browser): add browser_api.py — POST /run, GET /sessions, GET /action-log"
```

- [ ] Commit created.

---

## Task 14 — Mount `browser_router` in `app.py`

### Step 1 — Write the test

Add to `tests/test_browser_api.py`:

```python
def test_browser_router_mounted_in_app():
    """The browser router must be mounted in app.py."""
    app_path = Path(__file__).parent.parent / "src" / "bridge" / "app.py"
    content = app_path.read_text()
    assert "browser_api" in content or "browser_router" in content, \
        "browser_api not imported in app.py"
    assert "/api/browser" in content or "browser_router" in content, \
        "browser router not mounted in app.py"
```

- [ ] Add the `test_browser_router_mounted_in_app` test to `tests/test_browser_api.py`.

### Step 2 — Run test (expect failure)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_api.py::test_browser_router_mounted_in_app -v
# Expected: AssertionError
```

- [ ] Confirm test fails.

### Step 3 — Update `src/bridge/app.py`

Read `app.py` to find the correct insertion points (router imports and `include_router` calls). Then apply two edits:

**Edit 1 — add import** (near the scheduler imports, around line 51):

```python
from browser_api import router as browser_router, init_browser_api
```

**Edit 2 — include router** (find where `app.include_router(scheduler_router)` or similar calls are; add after):

```python
app.include_router(browser_router)
```

**Edit 3 — init at startup** (find the FastAPI startup event or `lifespan` function; add):

```python
if os.getenv("BROWSER_ENABLED", "false").lower() == "true":
    from agents.browser_agent import BrowserAgent  # noqa: PLC0415
    _browser_agent = BrowserAgent(run_chat_fn=run_chat_task)
    init_browser_api(browser_agent=_browser_agent, verify_token_dep=verify_token)
else:
    init_browser_api(browser_agent=None, verify_token_dep=verify_token)
```

- [ ] Read `src/bridge/app.py` lines 1–100 to find the exact insertion point for imports.
- [ ] Read the section where `include_router` calls are made.
- [ ] Add `from browser_api import router as browser_router, init_browser_api` to imports.
- [ ] Add `app.include_router(browser_router)` with the other router mounts.
- [ ] Add BrowserAgent init in the startup block.

### Step 4 — Run test (expect green)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_browser_api.py::test_browser_router_mounted_in_app -v
# Expected: 1 passed
```

- [ ] Test passes.

### Step 5 — Commit

```bash
git add src/bridge/app.py tests/test_browser_api.py
git commit -m "feat(browser): mount browser_router in app.py + BrowserAgent startup init"
```

- [ ] Commit created.

---

## Task 15 — Full test suite validation

### Step 1 — Run all browser tests

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest tests/test_migration_019.py tests/test_browser_agent.py tests/test_browser_api.py -v
# Expected: all tests pass
```

- [ ] All browser tests pass.

### Step 2 — Run full test suite (regression check)

```bash
cd /opt/nanobot-stack/rag-bridge && python -m pytest --tb=short -q
# Expected: no regressions in existing tests
```

- [ ] No regressions introduced.

### Step 3 — Run migration dry-run

```bash
python migrations/run_migrations.py --dry-run
# Expected includes: "Migration 19: 021_browser — would apply"
```

- [ ] Migration dry-run shows 021_browser.

### Step 4 — Final commit

```bash
git add .
git commit -m "feat(sub-project-k): complete BrowserAgent — Playwright automation, trust engine, action log, API"
```

- [ ] Final integration commit created.

---

## Docker / Deployment Notes

### Option A — Playwright sidecar (recommended for production)

Add to `docker-compose.yml`:

```yaml
  browser:
    image: mcr.microsoft.com/playwright/python:v1.44.0-focal
    restart: unless-stopped
    networks:
      - nanobot-net
    environment:
      - PLAYWRIGHT_WS_PORT=8765
    command: ["python", "-m", "playwright", "run-server", "--port", "8765"]
```

Set in bridge environment:

```env
BROWSER_DOCKER_SIDECAR=true
BROWSER_SIDECAR_WS_URL=ws://browser:8765
```

### Option B — Local install (simpler, adds ~300MB to bridge image)

Add to `bridge/Dockerfile`:

```dockerfile
RUN pip install playwright>=1.44 && playwright install chromium --with-deps
```

---

## Environment Variables Summary

| Variable | Default | Description |
|----------|---------|-------------|
| `BROWSER_ENABLED` | `false` | Opt-in master switch |
| `PLAYWRIGHT_BROWSER` | `chromium` | Browser engine (chromium only in production) |
| `BROWSER_ALLOWED_DOMAINS` | _(empty)_ | Comma-separated allowlist; empty = all domains allowed (WARNING logged) |
| `BROWSER_PAGE_TIMEOUT_MS` | `30000` | Page load timeout in ms (5000–120000) |
| `BROWSER_MAX_SESSION_S` | `300` | Max session duration in seconds |
| `BROWSER_SCREENSHOT_STORE` | `false` | Store screenshots in Qdrant browser_screenshots collection |
| `BROWSER_DOCKER_SIDECAR` | `false` | Connect to Playwright sidecar container instead of local launch |
| `BROWSER_SIDECAR_WS_URL` | `ws://browser:8765` | WebSocket URL of sidecar (only used when BROWSER_DOCKER_SIDECAR=true) |

---

## Checklist — All Deliverables

### Files to create
- [ ] `migrations/021_browser.py` — VERSION=19, check(), migrate(), WAL mode, browser.db
- [ ] `src/bridge/agents/browser_agent.py` — BrowserAgent, result dataclasses, exceptions, seed_default_trust_policies()
- [ ] `src/bridge/browser_api.py` — POST /api/browser/run, GET /api/browser/sessions, GET /api/browser/action-log
- [ ] `tests/test_migration_019.py` — 7 migration tests
- [ ] `tests/test_browser_agent.py` — ~32 tests across skeleton, allowlist, navigate, extract_text, click, fill, submit, screenshot, run()
- [ ] `tests/test_browser_api.py` — 5 API tests

### Files to modify
- [ ] `src/bridge/agents/__init__.py` — register "browser" in `_register_defaults()`
- [ ] `src/bridge/app.py` — import browser_api, include_router, init at startup
- [ ] `src/bridge/requirements.txt` — add `playwright>=1.44`

### Trust policies seeded
- [ ] `browser_read` → `notify_then_execute`, auto_promote_after=20
- [ ] `browser_fill` → `approval_required`, auto_promote_after=20
- [ ] `browser_submit` → `approval_required`, auto_promote_after=0 (NEVER auto-promoted)

### Post-deploy action required
- [ ] Run `playwright install chromium` in the deployment environment (or Dockerfile)
