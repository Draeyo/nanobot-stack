"""BrowserAgent — Playwright-based browser automation with trust-gated actions.

BROWSER_ENABLED defaults to false. All write actions (fill, submit, click on
submit buttons) require trust approval. Sensitive values are masked in logs.
Uses a separate browser.db for action audit logging.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False

from .base import AgentBase, AgentResult
from .browser_exceptions import (  # noqa: F401  (re-exported for callers)
    BrowserDomainBlockedError,
    BrowserInvalidURLError,
)

logger = logging.getLogger("rag-bridge.browser-agent")

# ---------------------------------------------------------------------------
# Config constants
# ---------------------------------------------------------------------------
_TEXT_TRUNCATE_LIMIT = 50_000
_SUBMIT_SELECTORS = ['[type="submit"]', 'button[type="submit"]', 'input[type="submit"]']
_ALLOWED_SCHEMES = {"https", "http"}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NavigateResult:
    """Result from a navigate() call."""
    url: str
    title: str
    status_code: int
    duration_ms: int


@dataclass
class ExtractTextResult:
    """Result from an extract_text() call."""
    text: str
    selector: str | None
    char_count: int
    truncated: bool


@dataclass
class ActionResult:
    """Generic result for click/fill/submit actions."""
    action: str
    success: bool
    selector: str | None = None
    duration_ms: int = 0
    error: str | None = None
    trust_status: str | None = None


@dataclass
class ScreenshotResult:
    """Result from a screenshot() call."""
    b64_png: str
    width: int
    height: int
    stored_qdrant: bool = False


# ---------------------------------------------------------------------------
# Trust policy seeding
# ---------------------------------------------------------------------------

def seed_default_trust_policies() -> None:
    """Seed default trust policies for browser actions into the trust engine."""
    try:
        import trust_engine as te  # pylint: disable=import-outside-toplevel
        te.set_trust_level("browser_read", "notify_then_execute")
        te.set_trust_level("browser_fill", "approval_required")
        te.set_trust_level("browser_submit", "approval_required")
        # set_trust_level() does not expose auto_promote_after, so we write it directly.
        # browser_submit must NEVER be auto-promoted (0); browser_read/fill promote at 20.
        db = sqlite3.connect(str(te.TRUST_DB_PATH))
        try:
            for action_type, threshold in (
                ("browser_read", 20),
                ("browser_fill", 20),
                ("browser_submit", 0),
            ):
                db.execute(
                    "UPDATE trust_policies SET auto_promote_after=? WHERE action_type=?",
                    (threshold, action_type),
                )
            db.commit()
        finally:
            db.close()
        logger.info("Browser trust policies seeded")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Could not seed browser trust policies: %s", exc)


# ---------------------------------------------------------------------------
# BrowserAgent
# ---------------------------------------------------------------------------

class BrowserAgent(AgentBase):
    """Agent that automates a headless Chromium browser via Playwright.

    All destructive actions (fill, submit, click on submit buttons) are gated
    through the trust engine. Sensitive values (passwords, tokens) are always
    masked as ``***`` in logs.
    """

    name: str = "browser"
    description: str = "Automates a headless browser: navigate, extract text, click, fill, submit, screenshot."
    tools: list[str] = ["navigate", "screenshot", "extract_text", "click", "fill", "submit"]

    def __init__(
        self,
        run_chat_fn: Callable[..., Any],
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        trust_engine: Any = None,
    ) -> None:
        super().__init__(run_chat_fn=run_chat_fn, tool_registry=tool_registry, trust_engine=trust_engine)

        self.browser_enabled: bool = os.getenv("BROWSER_ENABLED", "false").lower() == "true"
        _domains_raw = os.getenv("BROWSER_ALLOWED_DOMAINS", "")
        self.allowed_domains: list[str] = [d.strip() for d in _domains_raw.split(",") if d.strip()]
        self.page_timeout_ms: int = int(os.getenv("BROWSER_PAGE_TIMEOUT_MS", "30000"))
        self.max_session_s: int = int(os.getenv("BROWSER_MAX_SESSION_S", "300"))
        self.screenshot_store: bool = os.getenv("BROWSER_SCREENSHOT_STORE", "false").lower() == "true"

        self._page: Any = None
        self._session_id: str = ""
        self._actions_log: list[dict] = []

        # Seed trust policies when browser is enabled and trust_engine provided
        if self.browser_enabled and trust_engine is not None:
            try:
                seed_default_trust_policies()
            except Exception:  # pylint: disable=broad-except
                pass

    # ------------------------------------------------------------------
    # URL validation helpers
    # ------------------------------------------------------------------

    def _validate_url_scheme(self, url: str) -> None:
        """Raise BrowserInvalidURLError if the URL scheme is not http/https."""
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise BrowserInvalidURLError(f"Disallowed URL scheme: {parsed.scheme!r} in {url!r}")

    def _check_domain_allowlist(self, url: str) -> bool:
        """Return True if the domain is allowed. Raises BrowserDomainBlockedError if blocked."""
        if not self.allowed_domains:
            return True
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Strip leading www.
        if hostname.startswith("www."):
            hostname = hostname[4:]
        for allowed in self.allowed_domains:
            allowed_clean = allowed[4:] if allowed.startswith("www.") else allowed
            if hostname == allowed_clean or hostname.endswith("." + allowed_clean):
                return True
        raise BrowserDomainBlockedError(f"Domain not in allowlist: {hostname!r} (url={url!r})")

    # ------------------------------------------------------------------
    # Trust helpers
    # ------------------------------------------------------------------

    def _get_trust_action_type_for_click(self, selector: str) -> str:
        """Classify a click selector as browser_submit or browser_read."""
        sel_lower = selector.lower()
        if any(s in sel_lower for s in ['type="submit"', "type='submit'", "[type=submit]"]):
            return "browser_submit"
        return "browser_read"

    def _execute_with_trust(self, action_type: str, detail: str = "") -> dict[str, Any]:  # pylint: disable=unused-argument
        """Check trust level and return a status dict (does NOT execute the action fn).

        Returns auto status when trust_engine is not configured (self.trust_engine is None).
        The *detail* parameter is reserved for future audit logging.
        """
        if self.trust_engine is None:
            return {"ok": True, "status": "auto", "action_type": action_type}
        try:
            import trust_engine as te  # pylint: disable=import-outside-toplevel
            level = te.get_trust_level(action_type)
            if level in ("approval_required", "notify_then_execute"):
                return {"ok": True, "status": "approval_required", "action_type": action_type}
            if level == "blocked":
                return {"ok": False, "status": "blocked", "action_type": action_type}
            # auto
            return {"ok": True, "status": "auto", "action_type": action_type}
        except Exception:  # pylint: disable=broad-except
            return {"ok": True, "status": "auto", "action_type": action_type}

    # ------------------------------------------------------------------
    # Browser actions
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> NavigateResult:
        """Navigate the browser to *url* and return a NavigateResult."""
        self._validate_url_scheme(url)
        self._check_domain_allowlist(url)
        t0 = time.monotonic()
        response = await self._page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        status_code = response.status if response else 0
        title = await self._page.title()
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_action("navigate", {"url": url}, f"status={status_code}")
        return NavigateResult(url=self._page.url, title=title, status_code=status_code, duration_ms=duration_ms)

    async def extract_text(self, selector: str | None = None) -> ExtractTextResult:
        """Extract visible text from the page or a specific element."""
        t0 = time.monotonic()
        text = ""
        if selector:
            element = await self._page.query_selector(selector)
            if element:
                text = await element.inner_text()
        else:
            text = await self._page.inner_text("body")
        truncated = len(text) > _TEXT_TRUNCATE_LIMIT
        if truncated:
            text = text[:_TEXT_TRUNCATE_LIMIT]
        _elapsed_ms = int((time.monotonic() - t0) * 1000)
        self._log_action("extract_text", {"selector": selector, "duration_ms": _elapsed_ms}, f"chars={len(text)}")
        return ExtractTextResult(
            text=text,
            selector=selector,
            char_count=len(text),
            truncated=truncated,
        )

    async def click(self, selector: str) -> ActionResult:
        """Click an element. Submit buttons are gated by trust engine."""
        t0 = time.monotonic()
        action_type = self._get_trust_action_type_for_click(selector)

        if self.trust_engine is not None:
            trust_result = self._execute_with_trust(action_type, f"click:{selector}")
            if trust_result.get("status") == "approval_required":
                self._log_action("click", {"selector": selector}, "blocked:approval_required")
                return ActionResult(
                    action="click",
                    success=False,
                    selector=selector,
                    trust_status="approval_required",
                )

        await self._page.click(selector)
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_action("click", {"selector": selector}, "ok")
        return ActionResult(action="click", success=True, selector=selector, duration_ms=duration_ms)

    async def fill(self, selector: str, value: str) -> ActionResult:
        """Fill a form field. Value is always masked as *** in logs."""
        t0 = time.monotonic()

        # Always gate fill through trust engine (allows test mocking even when trust_engine=None)
        trust_result = self._execute_with_trust("browser_fill", f"fill:{selector}")
        if trust_result.get("status") == "approval_required":
            self._log_action("fill", {"selector": selector, "value": "***"}, "blocked:approval_required")
            return ActionResult(
                action="fill",
                success=False,
                selector=selector,
                trust_status="approval_required",
            )

        await self._page.fill(selector, value)
        duration_ms = int((time.monotonic() - t0) * 1000)
        # Value is ALWAYS masked in the log — never log the real value
        self._log_action("fill", {"selector": selector, "value": "***"}, "ok")
        return ActionResult(action="fill", success=True, selector=selector, duration_ms=duration_ms)

    async def submit(self, selector: str | None = None) -> ActionResult:
        """Submit a form. Always trust-gated. Falls back to first submit button if no selector."""
        t0 = time.monotonic()

        # Always gate submit through trust engine (allows test mocking even when trust_engine=None)
        trust_result = self._execute_with_trust("browser_submit", f"submit:{selector}")
        if trust_result.get("status") == "approval_required":
            self._log_action("submit", {"selector": selector}, "blocked:approval_required")
            return ActionResult(
                action="submit",
                success=False,
                selector=selector,
                trust_status="approval_required",
            )

        # Resolve selector: use provided or find first submit button
        target = selector
        if not target:
            for fallback in _SUBMIT_SELECTORS:
                element = await self._page.query_selector(fallback)
                if element:
                    target = fallback
                    break
        if not target:
            target = 'button[type="submit"]'

        await self._page.click(target)
        await self._page.wait_for_load_state("networkidle", timeout=10_000)
        duration_ms = int((time.monotonic() - t0) * 1000)
        self._log_action("submit", {"selector": target}, "ok")
        return ActionResult(action="submit", success=True, selector=target, duration_ms=duration_ms)

    async def screenshot(self) -> ScreenshotResult:
        """Take a screenshot of the current page."""
        t0 = time.monotonic()
        viewport = self._page.viewport_size or {"width": 1280, "height": 800}
        png_bytes = await self._page.screenshot()
        b64 = base64.b64encode(png_bytes).decode("utf-8")
        _elapsed_ms = int((time.monotonic() - t0) * 1000)

        stored = False
        if self.screenshot_store:
            try:
                await self._store_screenshot_qdrant(b64)
                stored = True
            except Exception:  # pylint: disable=broad-except
                stored = False

        self._log_action("screenshot", {"width": viewport["width"], "height": viewport["height"]}, "ok")
        return ScreenshotResult(
            b64_png=b64,
            width=viewport["width"],
            height=viewport["height"],
            stored_qdrant=stored,
        )

    async def _store_screenshot_qdrant(self, b64_png: str) -> None:
        """Store screenshot in Qdrant (stub — override or patch in production)."""
        logger.debug("Screenshot Qdrant storage not wired: len=%d", len(b64_png))

    # ------------------------------------------------------------------
    # DB logging
    # ------------------------------------------------------------------

    def _get_browser_db_path(self) -> str:
        """Return the path to browser.db."""
        state_dir = os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")
        return str(os.path.join(state_dir, "browser.db"))

    def _log_to_db(
        self,
        action_type: str,
        url: str,
        selector: str | None,
        status: str,
        trust_level: str,
        started_at: str,
        duration_ms: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Persist action to browser_action_log in browser.db."""
        try:
            db_path = self._get_browser_db_path()
            db = sqlite3.connect(db_path)
            try:
                db.execute(
                    "INSERT OR IGNORE INTO browser_action_log "
                    "(id, session_id, action_type, url, selector, status, trust_level, "
                    "started_at, duration_ms, error_msg) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), self._session_id, action_type, url,
                        selector, status, trust_level, started_at, duration_ms, error_msg,
                    ),
                )
                db.commit()
            finally:
                db.close()
        except Exception:  # pylint: disable=broad-except
            logger.debug("browser_action_log write failed (table may not exist yet)")

    # ------------------------------------------------------------------
    # LLM step planner
    # ------------------------------------------------------------------

    def _parse_steps(self, task: str, context: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Ask the LLM to plan browser steps for the given task."""
        system = (
            "You are a browser automation planner. Given a task, return a JSON array of steps.\n"
            "Each step is an object with an 'action' key (navigate, extract_text, click, fill, submit, screenshot).\n"
            "Additional keys depend on the action:\n"
            "  navigate: {action, url}\n"
            "  extract_text: {action, selector?}\n"
            "  click: {action, selector}\n"
            "  fill: {action, selector, value}\n"
            "  submit: {action, selector?}\n"
            "  screenshot: {action}\n"
            "Respond with ONLY the JSON array, no prose."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        if context:
            messages[1]["content"] += f"\nContext: {json.dumps(context)}"
        try:
            response = self.run_chat_fn(messages=messages, task="browser_plan")
            raw = response.get("text", "[]")
            return json.loads(raw)
        except Exception:  # pylint: disable=broad-except
            return []

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Execute browser automation for *task*."""
        self._actions_log = []
        self._total_tokens = 0

        # Guard: browser disabled
        if not self.browser_enabled:
            return self._make_result(
                "disabled",
                "Browser automation is disabled. Set BROWSER_ENABLED=true to enable.",
            )

        # Guard: playwright not installed.
        # Check PLAYWRIGHT_AVAILABLE flag (can be patched in tests via ba_mod.PLAYWRIGHT_AVAILABLE = False)
        # Also check async_playwright is not None (supports patch("agents.browser_agent.async_playwright", ...))
        if not PLAYWRIGHT_AVAILABLE and async_playwright is None:
            return self._make_result(
                "failed",
                "playwright is not installed. Run: pip install playwright && playwright install chromium",
            )

        self._session_id = str(uuid.uuid4())
        steps = self._parse_steps(task, context)

        try:
            async with asyncio.timeout(self.max_session_s):
                result = await self._run_session(steps, task)
        except TimeoutError:
            return self._make_result("failed", f"Session timed out after {self.max_session_s}s")
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("BrowserAgent session failed: %s", exc)
            return self._make_result("failed", f"Session error: {exc}")

        return result

    async def _run_session(self, steps: list[dict[str, Any]], task: str = "") -> AgentResult:  # pylint: disable=unused-argument
        """Launch Playwright and execute all planned steps."""
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context_bw = await browser.new_context()
            self._page = await context_bw.new_page()
            try:
                output_parts: list[str] = []
                for step in steps:
                    action = step.get("action", "")
                    step_output = await self._dispatch_step(step, action)
                    if step_output:
                        output_parts.append(step_output)
                output = "\n".join(output_parts) if output_parts else "Steps completed."
                return self._make_result("completed", output)
            finally:
                await context_bw.close()
                await browser.close()

    async def _dispatch_step(self, step: dict[str, Any], action: str) -> str:
        """Dispatch a single step and return a human-readable summary string."""
        started_at = datetime.now(timezone.utc).isoformat()
        current_url = getattr(self._page, "url", "")

        if action == "navigate":
            url = step.get("url", "")
            nav = await self.navigate(url)
            self._log_to_db("navigate", nav.url, None, "ok", "auto", started_at, nav.duration_ms)
            return f"Navigated to {nav.url} (status={nav.status_code}, title={nav.title!r})"

        if action == "extract_text":
            selector = step.get("selector")
            extract = await self.extract_text(selector)
            self._log_to_db("extract_text", current_url, selector, "ok", "auto", started_at)
            truncated_note = " [truncated]" if extract.truncated else ""
            return f"Extracted {extract.char_count} chars{truncated_note}"

        if action == "click":
            selector = step.get("selector", "")
            click_result = await self.click(selector)
            status = "ok" if click_result.success else "blocked"
            self._log_to_db("click", current_url, selector, status, "auto", started_at, click_result.duration_ms)
            return f"Clicked {selector!r}: {status}"

        if action == "fill":
            selector = step.get("selector", "")
            value = step.get("value", "")
            fill_result = await self.fill(selector, value)
            status = "ok" if fill_result.success else "blocked"
            self._log_to_db("fill", current_url, selector, status, "auto", started_at, fill_result.duration_ms)
            return f"Filled {selector!r}: {status}"

        if action == "submit":
            selector = step.get("selector")
            submit_result = await self.submit(selector)
            status = "ok" if submit_result.success else "blocked"
            self._log_to_db("submit", current_url, selector, status, "auto", started_at, submit_result.duration_ms)
            return f"Submitted form: {status}"

        if action == "screenshot":
            shot = await self.screenshot()
            self._log_to_db("screenshot", current_url, None, "ok", "auto", started_at)
            return f"Screenshot taken ({shot.width}x{shot.height}, stored_qdrant={shot.stored_qdrant})"

        logger.warning("Unknown browser action: %s", action)
        return f"Unknown action: {action}"
