"""Context-tiered prompt budget for small local models.

Local models (no native function calling) used to get the full prose tool
blocks + the full always-available set + an unbounded skill index regardless
of their context window. ``src.prompt_budget`` maps the resolved context
length to a tier that bounds all three; this pins the tier boundaries, the
override, and the helpers agent_loop uses to apply the budget.
"""
import pytest

from src.prompt_budget import PromptBudget, budget_for_context
from src.tool_index import ALWAYS_AVAILABLE, keyword_fallback_tools
from src.agent_loop import _assemble_prompt, _prep_event_payload, cap_skill_index


# ── tiers ──

@pytest.mark.parametrize("ctx,tier", [
    (8192, "tiny"),
    (8193, "small"),
    (16384, "small"),
    (16385, "medium"),
    (32768, "medium"),
    (32769, "full"),
    (None, "full"),
    (0, "full"),
])
def test_tier_boundaries(ctx, tier):
    assert budget_for_context(ctx).tier == tier


@pytest.mark.parametrize("override", ["tiny", "small", "medium", "full"])
def test_override_wins_over_context(override):
    # 1M context would be "full"; tiny/small/medium forced regardless.
    assert budget_for_context(1_000_000, override=override).tier == override
    # And upwards: an 4K window forced to full.
    assert budget_for_context(4096, override="full").tier == "full"


@pytest.mark.parametrize("override", ["auto", None, "", "bogus", "AUTO"])
def test_auto_or_unknown_override_uses_context(override):
    assert budget_for_context(4096, override=override).tier == "tiny"
    assert budget_for_context(20000, override=override).tier == "medium"


def test_override_is_case_insensitive():
    assert budget_for_context(4096, override="Full").tier == "full"


def test_tiny_always_available_is_the_four_tool_set():
    b = budget_for_context(4096)
    assert b.always_available == frozenset({"bash", "read_file", "web_search", "web_fetch"})
    assert b.tool_k == 3
    assert b.skill_index_max == 0
    assert b.tease_other_tools is False
    assert b.memory_k == 1


def test_small_excludes_admin_ish_tools():
    b = budget_for_context(12000)
    dropped = {"api_call", "list_served_models", "stop_served_model", "app_api"}
    assert b.always_available == ALWAYS_AVAILABLE - dropped
    assert not (b.always_available & dropped)
    assert b.tool_k == 5
    assert b.skill_index_max == 8
    assert b.tease_other_tools is False
    assert b.memory_k == 2


def test_medium_and_full_keep_full_always_available():
    m = budget_for_context(32768)
    f = budget_for_context(200_000)
    assert m.always_available == ALWAYS_AVAILABLE == f.always_available
    assert m.tool_k == f.tool_k == 8
    assert m.skill_index_max == 20
    assert f.skill_index_max == -1
    assert m.tease_other_tools and f.tease_other_tools
    assert m.memory_k == f.memory_k == 3


def test_budget_is_frozen():
    b = budget_for_context(None)
    assert isinstance(b, PromptBudget)
    with pytest.raises(Exception):
        b.tier = "tiny"  # type: ignore[misc]


# ── _assemble_prompt tease line ──

def test_assemble_prompt_tease_flag():
    tools = {"bash", "read_file"}
    with_tease = _assemble_prompt(tools, set(), tease_other_tools=True)
    without = _assemble_prompt(tools, set(), tease_other_tools=False)
    assert "(Other tools available when needed:" in with_tease
    assert "(Other tools available when needed:" not in without
    # Default keeps today's behaviour.
    assert "(Other tools available when needed:" in _assemble_prompt(tools, set())
    # Everything else is unchanged.
    assert "bash" in without and "read_file" in without


# ── keyword fallback (word boundaries, mirrors test_tool_index_keyword_boundaries) ──

def test_keyword_fallback_substring_inside_word_does_not_force_document_tools():
    for q in ("prefix the output with a label", "the deadline is online already"):
        tools = keyword_fallback_tools(q, ALWAYS_AVAILABLE)
        assert "edit_document" not in tools, q
        assert "update_document" not in tools, q


def test_keyword_fallback_substring_inside_word_does_not_force_email_tools():
    for q in ("i am replying to your github comment", "this document is unreadable"):
        tools = keyword_fallback_tools(q, ALWAYS_AVAILABLE)
        assert "send_email" not in tools, q
        assert "reply_to_email" not in tools, q


def test_keyword_fallback_substring_inside_word_does_not_force_serve_tools():
    tools = keyword_fallback_tools("please observe the reserve levels", ALWAYS_AVAILABLE)
    assert "serve_model" not in tools
    assert "serve_preset" not in tools


def test_keyword_fallback_genuine_keywords_still_force_include():
    assert "reply_to_email" in keyword_fallback_tools("reply to this email", ALWAYS_AVAILABLE)
    assert "edit_document" in keyword_fallback_tools("edit the document", ALWAYS_AVAILABLE)
    assert "serve_model" in keyword_fallback_tools("serve the model", ALWAYS_AVAILABLE)


def test_keyword_fallback_uses_given_always_set_not_global():
    tiny = budget_for_context(4096).always_available
    tools = keyword_fallback_tools("what is 2+2", tiny)
    assert tools == set(tiny)
    assert "app_api" not in tools and "python" not in tools


def test_keyword_fallback_schedule_intent_adds_manage_tasks():
    assert "manage_tasks" in keyword_fallback_tools("do this every dya at 7:30 am", set())


# ── skill index cap ──

def _entries(n):
    return [{"name": f"s{i}", "category": "c", "description": "d"} for i in range(n)]


def test_cap_skill_index_zero_omits_entirely():
    assert cap_skill_index(_entries(5), 0) is None


def test_cap_skill_index_negative_is_unlimited():
    e = _entries(50)
    assert cap_skill_index(e, -1) == e


def test_cap_skill_index_n_keeps_first_n_in_order():
    e = _entries(10)
    capped = cap_skill_index(e, 3)
    assert capped == e[:3]
    # Not truncated when under the cap.
    assert cap_skill_index(e, 20) == e
    assert cap_skill_index([], 3) == []


# ── agent_prep payload ──

def test_prep_event_payload_rounds_floats_and_tolerates_strings():
    payload = _prep_event_payload(
        {"request_setup": 0.123456, "tool_selection": 1.0},
        prompt_tier="tiny", tools_selected=4,
    )
    assert payload == {
        "request_setup": 0.123,
        "tool_selection": 1.0,
        "prompt_tier": "tiny",
        "tools_selected": 4,
    }
