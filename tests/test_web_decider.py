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


# ---------------------------------------------------------------------------
# Fix 1: URL + explicit search/recency intent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "Search for other coverage of https://example.com/story",
    "look up reactions to https://example.com/launch",
])
def test_url_with_explicit_search_intent_is_yes(msg):
    assert heuristic_decision(msg) == "yes"


def test_url_with_recency_is_ambiguous():
    assert heuristic_decision(
        "Summarize the latest news from https://example.com and compare to current coverage"
    ) == "ambiguous"


def test_bare_url_question_still_no():
    assert heuristic_decision("https://example.com/article — what does this say?") == "no"


# ---------------------------------------------------------------------------
# Fix 2: coding/schema vocab must not trigger web search
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "update the price field in my schema",
    "the stock SQLAlchemy model needs a new column",
    "add a forecast column to the dataframe",
    "schedule a cron job in python",
    "score this code review please",
    "my news feed component wont render",
])
def test_coding_vocab_not_web(msg):
    assert heuristic_decision(msg) == "no"


def test_no_web_verbs_beat_explicit_search_intent():
    """Deliberate precedence: self-contained work wins even with a search ask."""
    assert heuristic_decision("refactor this and search the web for examples") == "no"
