"""web_access param reaches resolve_web_access with the route's call shape."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_route_call_shape_auto_chat():
    from src.web_decider import resolve_web_access
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, allow_ws, decision = await resolve_web_access(
            "auto", "chat", "what's the latest python release?", None, None)
    assert use_web is True and decision == "auto-search"


@pytest.mark.asyncio
async def test_route_call_shape_form_strings():
    # Form values arrive as strings or None — must not crash.
    from src.web_decider import resolve_web_access
    use_web, allow_ws, decision = await resolve_web_access(
        "always", "agent", "hello", "true", None)
    assert allow_ws == "true"


@pytest.mark.asyncio
async def test_incognito_suppresses_web():
    """Incognito must not leak queries to search engines (mirrors RAG gating)."""
    from src.web_decider import resolve_web_access, apply_incognito
    use_web, allow_ws, decision = await resolve_web_access(
        "always", "chat", "latest news", None, None)
    use_web, decision = apply_incognito(True, use_web, decision)
    assert use_web is False
    assert decision == "incognito-off"
