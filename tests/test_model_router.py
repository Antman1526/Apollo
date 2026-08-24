"""Mixture routing: classifier boundaries and route_chat gating."""
import pytest

from services.model_router import LIGHT_MAX_CHARS, classify_message, route_chat


@pytest.mark.parametrize("msg", [
    "thanks!",
    "good morning",
    "what's the capital of France",
    "ok do it",
    "how are you today",
])
def test_light_messages(msg):
    assert classify_message(msg) == "light"


@pytest.mark.parametrize("msg", [
    "",                                          # empty → heavy (no routing)
    "x" * (LIGHT_MAX_CHARS + 1),                 # too long
    "please write a python function for this",   # heavy keyword
    "here:\n```\nprint(1)\n```",                 # code fence
    "why? and how? and when?",                   # multi-question
    "para one\n\npara two",                      # multi-paragraph
    "can you debug this error",                  # keyword
    "summarize the report",                      # keyword
])
def test_heavy_messages(msg):
    assert classify_message(msg) == "heavy"


def test_route_chat_disabled_returns_none(monkeypatch):
    monkeypatch.setattr("services.model_router.get_setting",
                        lambda k, d=None: False if k == "mixture_routing_enabled" else d)
    assert route_chat("thanks!") is None


def test_route_chat_routes_light_only(monkeypatch):
    monkeypatch.setattr("services.model_router.get_setting",
                        lambda k, d=None: True if k == "mixture_routing_enabled" else d)
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_endpoint",
        lambda prefix, owner=None: ("http://localhost:1", "tiny-model", {"h": "1"}),
    )
    assert route_chat("thanks!") == ("http://localhost:1", "tiny-model", {"h": "1"})
    assert route_chat("write me a python script please") is None  # heavy


def test_route_chat_unconfigured_returns_none(monkeypatch):
    monkeypatch.setattr("services.model_router.get_setting",
                        lambda k, d=None: True if k == "mixture_routing_enabled" else d)
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_endpoint",
        lambda prefix, owner=None: (None, None, None),
    )
    assert route_chat("thanks!") is None


def test_route_chat_never_raises(monkeypatch):
    monkeypatch.setattr("services.model_router.get_setting",
                        lambda k, d=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert route_chat("thanks!") is None
