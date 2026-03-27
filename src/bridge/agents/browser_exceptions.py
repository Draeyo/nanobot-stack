"""Browser agent custom exceptions — defined in a separate module for stability.

Keeping exceptions separate prevents identity issues when browser_agent module
is reloaded in tests (sys.modules cache cleared for 'browser_agent' keys only).
"""
from __future__ import annotations


class BrowserDomainBlockedError(Exception):
    """Raised when a URL's domain is not in the allowlist."""


class BrowserInvalidURLError(Exception):
    """Raised when a URL has a disallowed scheme (file://, javascript:, etc.)."""


class BrowserNotReadyError(Exception):
    """Raised when the browser page is not initialised."""
