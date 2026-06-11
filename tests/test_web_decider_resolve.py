"""resolve_web_access maps tri-state mode onto use_web/allow_web_search."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.web_decider import _ask_utility_model, decide_use_web, resolve_web_access


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
