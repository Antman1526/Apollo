"""resolve_web_access maps tri-state mode onto use_web/allow_web_search."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.web_decider import (
    _ask_utility_model,
    _is_short_follow_up,
    decide_use_web,
    resolve_web_access,
)


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
async def test_settings_fallback_respects_explicit_legacy_flags(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "auto"})
    # Old client explicitly asked for web — never override with the decider.
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "write a poem", "true", None)
    assert use_web == "true"
    assert decision is None


@pytest.mark.asyncio
async def test_settings_off_applies_when_no_legacy_intent(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "off"})
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "latest news", None, None)
    assert use_web is False
    assert decision == "off"


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


# ---------------------------------------------------------------------------
# _ask_utility_model — self-protection + shape-tolerant parsing
# ---------------------------------------------------------------------------

def _make_http_mock(json_body: dict):
    """Build an AsyncMock httpx.AsyncClient whose .post() returns json_body."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=json_body)

    post_mock = AsyncMock(return_value=response)
    client_instance = MagicMock()
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_instance.post = post_mock

    client_class = MagicMock(return_value=client_instance)
    return client_class, post_mock


@pytest.mark.asyncio
async def test_ask_utility_no_endpoint_returns_none_no_http(monkeypatch):
    """Self-guard: missing utility_endpoint_id → None without any HTTP call."""
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": ""})
    client_class, post_mock = _make_http_mock({})
    with patch("httpx.AsyncClient", client_class):
        result = await _ask_utility_model("Who is the CEO of Anthropic?")
    assert result is None
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ask_utility_openai_shape_yes(monkeypatch):
    """OpenAI-shaped response → True."""
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    body = {"choices": [{"message": {"content": "YES"}}]}
    client_class, _ = _make_http_mock(body)
    with patch("httpx.AsyncClient", client_class), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("http://fake/v1/chat/completions", "gpt-4o-mini", {})):
        result = await _ask_utility_model("Who is the CEO of Anthropic?")
    assert result is True


@pytest.mark.asyncio
async def test_ask_utility_ollama_shape_no(monkeypatch):
    """Ollama-shaped response {"message": {"content": "NO"}} → False."""
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    body = {"message": {"content": "NO"}}
    client_class, _ = _make_http_mock(body)
    with patch("httpx.AsyncClient", client_class), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("http://fake/api/chat", "llama3", {})):
        result = await _ask_utility_model("Who is the CEO of Anthropic?")
    assert result is False


@pytest.mark.asyncio
async def test_ask_utility_anthropic_shape_yes(monkeypatch):
    """Anthropic-shaped response {"content":[{"type":"text","text":"YES"}]} → True."""
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    body = {"content": [{"type": "text", "text": "YES"}]}
    client_class, _ = _make_http_mock(body)
    with patch("httpx.AsyncClient", client_class), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("http://fake/v1/messages", "claude-haiku-4-5", {})):
        result = await _ask_utility_model("Who is the CEO of Anthropic?")
    assert result is True


@pytest.mark.asyncio
async def test_ask_utility_garbage_body_returns_none(monkeypatch):
    """Unrecognised response shape → None (not an exception)."""
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    body = {}
    client_class, _ = _make_http_mock(body)
    with patch("httpx.AsyncClient", client_class), \
         patch("src.endpoint_resolver.resolve_endpoint",
               return_value=("http://fake/v1/chat/completions", "m", {})):
        result = await _ask_utility_model("Who is the CEO of Anthropic?")
    assert result is None


# ---------------------------------------------------------------------------
# Task 8: Follow-up decider — short follow-ups inherit web context
# ---------------------------------------------------------------------------

def test_is_short_follow_up_matches_cues():
    assert _is_short_follow_up("and what about tomorrow?") is True
    assert _is_short_follow_up("what about next week?") is True
    assert _is_short_follow_up("how about the same for Paris?") is True
    assert _is_short_follow_up("also include Tuesday") is True
    assert _is_short_follow_up("ok and what's the schedule?") is True
    assert _is_short_follow_up("more details please") is True
    assert _is_short_follow_up("any update on that?") is True


def test_is_short_follow_up_rejects_long_messages():
    long_msg = "and what about " + "x" * 120
    assert _is_short_follow_up(long_msg) is False


def test_is_short_follow_up_rejects_no_cue():
    assert _is_short_follow_up("what is the capital of France?") is False
    assert _is_short_follow_up("refactor this function") is False
    assert _is_short_follow_up("") is False


@pytest.mark.asyncio
async def test_follow_up_inherits_web_context():
    """A short follow-up after a weather query should trigger search."""
    with patch("src.web_decider._ask_utility_model", new=AsyncMock(return_value=None)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "and what about tomorrow?", None, None,
            prev_message="weather in Stockholm today")
    assert use_web is True
    assert decision == "auto-search"


@pytest.mark.asyncio
async def test_follow_up_without_web_context_stays_no():
    """A short follow-up after a code question should not trigger search."""
    use_web, _, decision = await resolve_web_access(
        "auto", "chat", "and what about the second function?", None, None,
        prev_message="refactor this function to use a dict")
    assert use_web is False


@pytest.mark.asyncio
async def test_prev_message_ignored_when_not_follow_up():
    """A normal (non-follow-up) message with prev_message behaves as before."""
    use_web, _, decision = await resolve_web_access(
        "auto", "chat", "write a poem about autumn", None, None,
        prev_message="weather in Stockholm today")
    assert use_web is False


@pytest.mark.asyncio
async def test_prev_message_empty_string_is_safe():
    """Explicit empty prev_message must not crash or trigger follow-up path."""
    use_web, _, decision = await resolve_web_access(
        "auto", "chat", "and what about tomorrow?", None, None,
        prev_message="")
    # No prev context → can't infer web need → should stay False
    assert use_web is False
