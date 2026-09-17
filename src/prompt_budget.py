"""
Context-tiered prompt budget for agent mode.

Local models without native function calling get the full prose
``TOOL_SECTIONS`` blocks (~24 KB for every tool) plus the always-available
set plus an unbounded skill index — regardless of their context window.
An 8K model can burn most of its context on the tool catalogue before the
conversation even starts.

``budget_for_context`` maps a resolved context window to a tier that bounds
how many RAG-retrieved tools are shown, which tools are always present,
whether the "(Other tools available ...)" tease is included, how many skill
index entries are listed, and how many recalled memories to inject.
"""

from dataclasses import dataclass
from typing import FrozenSet, Optional

TIERS = ("tiny", "small", "medium", "full")

# Tier boundaries (inclusive upper bound on context_length).
TINY_MAX = 8192
SMALL_MAX = 16384
MEDIUM_MAX = 32768

_TINY_ALWAYS = frozenset({"bash", "read_file", "web_search", "web_fetch"})
# Admin-ish tools the small tier drops from the always-on set: they are
# contextual (cookbook / integrations / generic API loopback) and fire via
# keyword hints or retrieval when the user actually talks about them.
_SMALL_EXCLUDED = frozenset({"api_call", "list_served_models", "stop_served_model", "app_api"})


@dataclass(frozen=True)
class PromptBudget:
    tier: str
    tool_k: int
    always_available: FrozenSet[str]
    skill_index_max: int  # 0 = omit index entirely; -1 = unlimited
    tease_other_tools: bool
    memory_k: int


def _tier_for(context_length: Optional[int]) -> str:
    try:
        n = int(context_length or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return "full"  # unknown window — don't restrict
    if n <= TINY_MAX:
        return "tiny"
    if n <= SMALL_MAX:
        return "small"
    if n <= MEDIUM_MAX:
        return "medium"
    return "full"


def budget_for_context(context_length: Optional[int], *, override: Optional[str] = None) -> PromptBudget:
    """Resolve the prompt budget for a context window.

    ``override`` (the ``agent_prompt_tier`` setting) forces a tier when it is
    one of tiny/small/medium/full; ``auto``/None/unknown values mean "by
    context".
    """
    # Lazy import: tool_index pulls in observability/numpy at import time and
    # this module should stay cheap to import from anywhere.
    from src.tool_index import ALWAYS_AVAILABLE

    tier = str(override or "auto").strip().lower()
    if tier not in TIERS:
        tier = _tier_for(context_length)

    if tier == "tiny":
        return PromptBudget(
            tier="tiny", tool_k=3, always_available=_TINY_ALWAYS,
            skill_index_max=0, tease_other_tools=False, memory_k=1,
        )
    if tier == "small":
        return PromptBudget(
            tier="small", tool_k=5, always_available=frozenset(ALWAYS_AVAILABLE - _SMALL_EXCLUDED),
            skill_index_max=8, tease_other_tools=False, memory_k=2,
        )
    if tier == "medium":
        return PromptBudget(
            tier="medium", tool_k=8, always_available=frozenset(ALWAYS_AVAILABLE),
            skill_index_max=20, tease_other_tools=True, memory_k=3,
        )
    return PromptBudget(
        tier="full", tool_k=8, always_available=frozenset(ALWAYS_AVAILABLE),
        skill_index_max=-1, tease_other_tools=True, memory_k=3,
    )
