"""Tests for BrowserAgent — full Playwright mock suite."""
from __future__ import annotations

import importlib
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_playwright_in_requirements():
    """playwright>=1.44 must be listed in requirements.txt."""
    req_path = (
        __import__("pathlib").Path(__file__).parent.parent
        / "src" / "bridge" / "requirements.txt"
    )
    content = req_path.read_text()
    assert "playwright" in content, "playwright not found in requirements.txt"


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


def test_allowlist_empty_allows_all(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
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


def _make_mock_page(url="https://example.com", title="Example", status=200):
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
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"})
    assert agent._get_trust_action_type_for_click('[type="submit"]') == "browser_submit"
    assert agent._get_trust_action_type_for_click("#login-btn") == "browser_read"


@pytest.mark.asyncio
async def test_click_blocked_by_trust_engine(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    from agents.browser_agent import ActionResult
    mock_te = MagicMock()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=mock_te)
    agent._execute_with_trust = MagicMock(return_value={
        "ok": True, "status": "approval_required"
    })
    page = _make_mock_page()
    agent._page = page
    agent._session_id = "test-session"
    result = await agent.click("#submit")
    assert result.success is False
    page.click.assert_not_called()


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


@pytest.mark.asyncio
async def test_submit_always_requires_approval_by_default(monkeypatch):
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
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": "[]"}, trust_engine=None)
    page = _make_mock_page()
    page.query_selector = AsyncMock(return_value=MagicMock())
    agent._page = page
    agent._session_id = "test-session"
    result = await agent.submit(None)
    assert result.success is True
    page.click.assert_called_once()


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
    assert result.stored_qdrant is False


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
    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        # Simulate PLAYWRIGHT_AVAILABLE=False by patching the module-level flag
        import agents.browser_agent as ba_mod
        orig = ba_mod.PLAYWRIGHT_AVAILABLE
        ba_mod.PLAYWRIGHT_AVAILABLE = False
        try:
            result = await agent.run("navigate to example.com")
        finally:
            ba_mod.PLAYWRIGHT_AVAILABLE = orig
    assert result.status == "failed"
    assert "playwright" in result.output.lower()


@pytest.mark.asyncio
async def test_run_executes_navigate_step(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    monkeypatch.setenv("BROWSER_ALLOWED_DOMAINS", "example.com")
    BrowserAgent = _import_browser_agent()
    agent = BrowserAgent(run_chat_fn=lambda *a, **k: {"text": '[{"action": "navigate", "url": "https://example.com"}]'})
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
    with patch("agents.browser_agent.async_playwright", return_value=mock_pw):
        result = await agent.run("navigate to example.com")
    assert result.status == "completed"
    assert len(result.actions_taken) > 0


@pytest.mark.asyncio
async def test_run_records_actions_in_actions_taken(monkeypatch):
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


def test_browser_agent_registered_in_registry(monkeypatch):
    monkeypatch.setenv("BROWSER_ENABLED", "true")
    import importlib
    import agents as agents_pkg
    importlib.reload(agents_pkg)
    from agents import AGENT_REGISTRY
    assert "browser" in AGENT_REGISTRY


def test_browser_agent_default_trust_policies():
    import sys, importlib, tempfile, os
    sys.path.insert(0, str(
        __import__("pathlib").Path(__file__).parent.parent / "src" / "bridge"
    ))
    import trust_engine
    from agents.browser_agent import seed_default_trust_policies
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch_env = {"RAG_STATE_DIR": tmp, "TRUST_ENGINE_ENABLED": "true"}
        original = {k: os.environ.get(k) for k in monkeypatch_env}
        os.environ.update(monkeypatch_env)
        try:
            importlib.reload(trust_engine)
            seed_default_trust_policies()
            submit_level = trust_engine.get_trust_level("browser_submit")
            read_level = trust_engine.get_trust_level("browser_read")
            fill_level = trust_engine.get_trust_level("browser_fill")
            # Capture policies while the temp DB is still alive
            all_policies = {p["action_type"]: p for p in trust_engine.get_policies()}
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    assert submit_level == "approval_required"
    assert read_level == "notify_then_execute"
    assert fill_level == "approval_required"
    # browser_submit must NEVER be auto-promoted
    assert all_policies["browser_submit"]["auto_promote_after"] == 0
