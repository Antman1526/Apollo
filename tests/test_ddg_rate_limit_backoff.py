"""Task 9: RateLimitError must not be retried within the same provider.

Patching _call_provider to raise RateLimitError should result in exactly one
call attempt for that provider (no instant re-try), then fall through to the
next provider in the chain.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.search.analytics import RateLimitError


def _settings(**over):
    base = {
        "search_provider": "searxng",
        "search_fallback_chain": ["duckduckgo"],
        "search_url": "",
        "searxng_managed": False,  # unmanaged so we don't need a sidecar runtime
    }
    base.update(over)
    return base


def test_rate_limit_error_breaks_retry_loop_searxng_search(tmp_path):
    """RateLimitError on searxng_search_results must attempt the provider
    exactly once, then fall through to the next provider."""
    call_counts = {"searxng": 0, "duckduckgo": 0}

    def fake_call_provider(provider_name, query, count, time_filter=None):
        call_counts[provider_name] = call_counts.get(provider_name, 0) + 1
        if provider_name == "searxng":
            raise RateLimitError("429 Too Many Requests")
        return [{"title": "DDG result", "url": "https://example.com"}]

    # Use a unique tmp_path subdir per test so we never hit an existing cache
    cache_dir = tmp_path / "search_cache"
    cache_dir.mkdir()

    with patch("services.search.core._get_search_settings",
               return_value=_settings()), \
         patch("services.search.core._call_provider",
               side_effect=fake_call_provider), \
         patch("services.search.core._build_provider_chain",
               return_value=["searxng", "duckduckgo"]), \
         patch("services.search.core._record_query"), \
         patch("services.search.core.rank_search_results", side_effect=lambda q, r: r), \
         patch("services.search.core.SEARCH_CACHE_DIR", cache_dir):
        from services.search.core import searxng_search_results
        results = searxng_search_results("test query ratelimit unique 1234")

    # searxng should have been tried exactly once (no retry after 429)
    assert call_counts["searxng"] == 1, (
        f"Expected 1 attempt for searxng but got {call_counts['searxng']}"
    )
    # duckduckgo should have been tried and succeeded
    assert call_counts["duckduckgo"] == 1
    assert len(results) == 1


def test_rate_limit_error_breaks_retry_loop_comprehensive_search():
    """RateLimitError on comprehensive_web_search must attempt the provider
    exactly once, then fall through to the next provider."""
    call_counts = {"searxng": 0, "duckduckgo": 0}

    def fake_call_provider(provider_name, query, count, time_filter=None):
        call_counts[provider_name] = call_counts.get(provider_name, 0) + 1
        if provider_name == "searxng":
            raise RateLimitError("429 Too Many Requests")
        return [{"title": "DDG result", "url": "https://example.com"}]

    with patch("services.search.core._get_search_settings",
               return_value=_settings()), \
         patch("services.search.core._call_provider",
               side_effect=fake_call_provider), \
         patch("services.search.core._build_provider_chain",
               return_value=["searxng", "duckduckgo"]), \
         patch("services.search.core.rank_search_results", side_effect=lambda q, r: r):
        from services.search.core import comprehensive_web_search
        # comprehensive_web_search returns str or (str, list) — we just need it
        # not to raise and to have called searxng only once
        try:
            comprehensive_web_search("test query ratelimit comprehensive")
        except Exception:
            pass  # content fetching may fail in test; that's fine

    assert call_counts["searxng"] == 1, (
        f"Expected 1 attempt for searxng but got {call_counts['searxng']}"
    )
    assert call_counts["duckduckgo"] >= 1
