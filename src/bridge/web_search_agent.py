"""Top-level re-export of WebSearchAgent for direct import compatibility."""
# pylint: disable=unused-import
from agents.web_search_agent import (  # noqa: F401
    WebSearchAgent,
    WebSearchRateLimitError,
    WebSearchUnavailableError,
    WebSearchDisabledError,
    SearchResult,
    VALID_CATEGORIES,
)
