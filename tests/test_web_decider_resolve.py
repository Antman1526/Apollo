"""resolve_web_access maps tri-state mode onto use_web/allow_web_search."""
from unittest.mock import AsyncMock, patch

import pytest

from src.web_decider import decide_use_web, resolve_web_access


@pytest.mark.asyncio
async def test_manual_mode_passes_through(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "manual"})
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "hello", "true", None)
    assert use_web == "true"          # untouched legacy flag
    assert allow_ws is None
    assert decision is None


@pytest.mark.asyncio
async def test_off_disables_everything():
    use_web, allow_ws, decision = await resolve_web_access(
        "off", "agent", "latest news", "true", "true")
    assert use_web is False
    assert allow_ws == "false"
    assert decision == "off"


@pytest.mark.asyncio
async def test_always_chat_sets_use_web():
    use_web, allow_ws, _ = await resolve_web_access(
        "always", "chat", "hello", None, None)
    assert use_web is True


@pytest.mark.asyncio
async def test_always_agent_enables_tools():
    use_web, allow_ws, _ = await resolve_web_access(
        "always", "agent", "hello", None, None)
    assert allow_ws == "true"


@pytest.mark.asyncio
async def test_auto_agent_enables_tools_without_presearch():
    use_web, allow_ws, decision = await resolve_web_access(
        "auto", "agent", "write a poem", None, None)
    assert allow_ws == "true"
    assert decision == "auto-tools"


@pytest.mark.asyncio
async def test_auto_chat_searches_when_decider_says_yes():
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "latest news", None, None)
    assert use_web is True
    assert decision == "auto-search"


@pytest.mark.asyncio
async def test_auto_chat_skips_when_decider_says_no():
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=False)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "write a poem", None, None)
    assert use_web is False
    assert decision == "auto-skip"


@pytest.mark.asyncio
async def test_settings_auto_applies_when_param_missing(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "auto"})
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, _, decision = await resolve_web_access(
            None, "chat", "latest news", None, None)
    assert use_web is True


@pytest.mark.asyncio
async def test_decide_yes_no_skip_utility():
    # Clear heuristic verdicts never call the utility model.
    with patch("src.web_decider._ask_utility_model", new=AsyncMock()) as ask:
        assert await decide_use_web("latest news on rust") is True
        assert await decide_use_web("write a poem") is False
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_decide_ambiguous_uses_utility_when_configured(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    with patch("src.web_decider._ask_utility_model", new=AsyncMock(return_value=True)):
        assert await decide_use_web("Who is the CEO of Anthropic?") is True


@pytest.mark.asyncio
async def test_decide_ambiguous_defaults_no_without_utility(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": ""})
    assert await decide_use_web("Who is the CEO of Anthropic?") is False
