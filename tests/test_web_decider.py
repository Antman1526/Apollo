"""Heuristic web-need classification for web_access=auto."""
import pytest

from src.web_decider import heuristic_decision


@pytest.mark.parametrize("msg", [
    "What's the latest news on the EU AI Act?",
    "Search for reviews of the Framework 16 laptop",
    "current price of AMD stock",
    "weather in Stockholm today",
    "When is the next SpaceX launch scheduled?",
    "look up the Python 3.14 release notes",
])
def test_clear_yes(msg):
    assert heuristic_decision(msg) == "yes"


@pytest.mark.parametrize("msg", [
    "Write me a poem about autumn",
    "Refactor this function to use a dict",
    "Translate 'good morning' to Swedish",
    "def f(x):\n    return x + 1\n```python\nfix this\n```",
    "Summarize the following text: Lorem ipsum dolor",
    "https://example.com/article — what does this say?",  # URL: auto-fetch handles it
])
def test_clear_no(msg):
    assert heuristic_decision(msg) == "no"


@pytest.mark.parametrize("msg", [
    "Who is the CEO of Anthropic?",
    "How many people live in Reykjavik?",
])
def test_ambiguous(msg):
    assert heuristic_decision(msg) == "ambiguous"


def test_empty_is_no():
    assert heuristic_decision("") == "no"
    assert heuristic_decision(None) == "no"


def test_long_paste_is_no():
    assert heuristic_decision("latest news " + "x" * 4000) == "no"
