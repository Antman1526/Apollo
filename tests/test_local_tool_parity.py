"""Every agent tool must be teachable to LOCAL models, not just API models.

API models learn tools from FUNCTION_TOOL_SCHEMAS (native function calling).
Local models learn them from agent_loop.TOOL_SECTIONS (fenced-block prompt
text). A tool present only in the schemas is INVISIBLE to local models — in a
local-first app that is a real feature gap, and exactly how reference_search
and python_session shipped unusable by local models: an 8B model asked to use
reference_search simply hallucinated a weather result.

New tools must either get a TOOL_SECTIONS entry or be explicitly added to the
known-gap list below with a reason.
"""
import pytest

# Tools deliberately (or historically) not taught to local models. Each needs
# a reason; shrinking this list is welcome, growing it needs thought.
KNOWN_SCHEMA_ONLY = {
    "api_call",              # pre-existing gap; candidate for a future section
    "trigger_research",      # local path routes research via manage_research
    "edit_image",            # pre-existing gap
    "adopt_served_model",    # cookbook/admin-heavy; deliberate API-only for now
    "list_cookbook_servers", # cookbook/admin-heavy
    "list_serve_presets",    # cookbook/admin-heavy
    "serve_preset",          # cookbook/admin-heavy
}


def _load():
    import src.agent_tools  # noqa: F401  (resolves the schemas/tools import cycle)
    from src.agent_loop import TOOL_SECTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS if "function" in t}
    return names, set(TOOL_SECTIONS.keys())


def test_schema_tools_have_local_prompt_sections():
    schema_names, sections = _load()
    missing = schema_names - sections - KNOWN_SCHEMA_ONLY
    assert not missing, (
        f"Tools invisible to local models (add a TOOL_SECTIONS entry in "
        f"src/agent_loop.py, or list them in KNOWN_SCHEMA_ONLY with a reason): "
        f"{sorted(missing)}"
    )


def test_known_gap_list_is_not_stale():
    """Entries here that HAVE gained sections should be removed from the list."""
    _schema_names, sections = _load()
    stale = KNOWN_SCHEMA_ONLY & sections
    assert not stale, f"remove from KNOWN_SCHEMA_ONLY (now have sections): {sorted(stale)}"


@pytest.mark.parametrize("tool", ["reference_search", "python_session"])
def test_new_tools_are_taught_to_local_models(tool):
    _schema_names, sections = _load()
    assert tool in sections, f"{tool} must be teachable to local models"
    from src.agent_loop import TOOL_SECTIONS
    text = TOOL_SECTIONS[tool]
    assert f"```{tool}" in text, f"{tool} section must show the fenced syntax"


def test_new_tools_are_retrievable_by_rag_selection():
    """A TOOL_SECTIONS entry is useless if RAG selection can never pick it:
    retrieval embeds tool_index.BUILTIN_TOOL_DESCRIPTIONS, a THIRD registry.
    reference_search shipped in schemas+dispatch but was absent from both the
    local prompt AND this registry — so local models could never see it."""
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    for tool in ("reference_search", "python_session"):
        assert tool in BUILTIN_TOOL_DESCRIPTIONS, (
            f"{tool} missing from tool_index.BUILTIN_TOOL_DESCRIPTIONS — "
            f"RAG selection can never include its prompt section"
        )


def test_reference_search_keyword_hint_fires():
    """The typo-resilient fallback path: catalog-intent phrases force-include
    the tool even when embedding retrieval misses."""
    from src.tool_index import ToolIndex
    hints = ToolIndex._KEYWORD_HINTS
    hit = [tools for kws, tools in hints.items() if "free api" in kws]
    assert hit and "reference_search" in hit[0]


def test_new_tools_are_in_tool_tags():
    """TOOL_TAGS is the FOURTH registry: the fenced-block regex is built from
    it, and native function calls are rejected as unknown without it. A tool
    absent here can never be parsed from model output AT ALL — dispatch tests
    that hand-build ToolBlocks bypass this and give false confidence."""
    import src.agent_tools
    from src.agent_tools import TOOL_TAGS
    for tool in ("reference_search", "python_session"):
        assert tool in TOOL_TAGS, f"{tool} missing from TOOL_TAGS — unparseable"


def test_qwen_function_eq_dialect_parses():
    """Live regression: Qwen3-365-A3B emits `<function=NAME><parameter=KEY>`
    (the Qwen/Llama-3 chat-template dialect) with a stray </tool_call>. It
    must normalize into a parsed block, not fall through as prose."""
    import src.agent_tools  # noqa: F401 (import-cycle entry point)
    from src.tool_parsing import parse_tool_blocks

    txt = (
        "I'll search first.\n\n<function=reference_search>\n"
        "<parameter=query>\nfree weather API\n</parameter>\n</function>\n</tool_call>"
    )
    blocks = parse_tool_blocks(txt)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "reference_search"
    assert "free weather API" in blocks[0].content


def test_fenced_new_tools_parse():
    import src.agent_tools  # noqa: F401
    from src.tool_parsing import parse_tool_blocks

    fb = parse_tool_blocks('```reference_search\n{"query": "weather"}\n```')
    assert fb and fb[0].tool_type == "reference_search"
    ps = parse_tool_blocks('```python_session\nx = 1\n```')
    assert ps and ps[0].tool_type == "python_session"
