"""Pin the sidebar's Work/Know split of the old flat "Tools" section.

The Tools section grew into a flat list of 12 unrelated shortcuts. This locks
in the split into two sections that keeps the same DOM id for the first one
(`tools-section`, retitled "Work") so collapse/reorder state keyed by that id
survives, plus a new sibling (`know-section`, titled "Know") that follows it.

Plain text/regex assertions (no bs4 dependency), matching the lightweight
style of the other tests in this suite (see test_dialog_aria.py).
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_APP_JS = (_REPO / "static" / "app.js").read_text(encoding="utf-8")

WORK_IDS = [
    "tool-calendar-btn",
    "tool-notes-btn",
    "tool-tasks-btn",
    "tool-browser-btn",
    "tool-gallery-btn",
    "tool-paperclip-btn",
    # Theme predates the Work/Know split and isn't one of the 12 redistributed
    # tools — it intentionally stays put as the trailing item under Work.
    "tool-theme-btn",
]
KNOW_IDS = [
    "tool-memory-btn",
    "tool-research-btn",
    "tool-library-btn",
    "tool-cookbook-btn",
    "tool-compare-btn",
    "tool-activity-btn",
]
ALL_IDS = WORK_IDS + KNOW_IDS
# Matches only the ids being asserted on here, in whatever order they appear.
_TOOL_ID_RE = re.compile(r'id="(' + "|".join(re.escape(i) for i in ALL_IDS) + r')"')


def _section_bounds():
    """Locate the tools-section / know-section chunks by slicing on markers.

    tools-section: from its own opening <div ...> to know-section's opening tag.
    know-section: from its opening tag to the sidebar-user-bar that follows
    every sidebar section.

    The opening-tag patterns tolerate extra attributes landing on the div
    (e.g. a future `draggable="true"`) so an unrelated markup change gives a
    readable assertion failure here instead of a raw ValueError from `.index`.
    """
    work_start_re = re.compile(r'<div class="section"[^>]*id="tools-section"')
    know_start_re = re.compile(r'<div class="section"[^>]*id="know-section"')
    end_marker = 'id="sidebar-user-bar"'

    work_match = work_start_re.search(_INDEX)
    assert work_match, "could not find the tools-section opening <div> in index.html"
    know_match = know_start_re.search(_INDEX)
    assert know_match, "could not find the know-section opening <div> in index.html"

    work_start = work_match.start()
    know_start = know_match.start()
    end = _INDEX.index(end_marker)

    assert work_start < know_start < end, (
        "expected tools-section, then know-section, then the sidebar user bar, in that order"
    )

    work_chunk = _INDEX[work_start:know_start]
    know_chunk = _INDEX[know_start:end]
    return work_chunk, know_chunk


def _section_title_text(chunk):
    m = re.search(r'<span class="section-title"[^>]*>(.*?)</span>', chunk, re.DOTALL)
    assert m, "could not find a .section-title span in section chunk"
    # Strip the leading icon <svg>...</svg> to get the plain title text, then
    # collapse whitespace.
    inner = re.sub(r'<svg.*?</svg>', '', m.group(1), flags=re.DOTALL)
    return inner.strip()


def test_tools_section_id_survives_and_is_retitled_work():
    work_chunk, _ = _section_bounds()
    assert _section_title_text(work_chunk) == "Work"


def test_know_section_exists_and_is_titled_know():
    _, know_chunk = _section_bounds()
    assert _section_title_text(know_chunk) == "Know"


def test_work_section_contains_exactly_the_expected_tools_in_order():
    work_chunk, _ = _section_bounds()
    found = _TOOL_ID_RE.findall(work_chunk)
    assert found == WORK_IDS


def test_know_section_contains_exactly_the_expected_tools_in_order():
    _, know_chunk = _section_bounds()
    found = _TOOL_ID_RE.findall(know_chunk)
    assert found == KNOW_IDS


def test_each_redistributed_tool_id_appears_exactly_once_in_the_file():
    for tool_id in ALL_IDS:
        count = len(re.findall(r'id="%s"' % re.escape(tool_id), _INDEX))
        assert count == 1, f"expected exactly one id={tool_id!r} in index.html, found {count}"


def test_paperclip_and_activity_stay_hidden_by_default():
    for tool_id in ("tool-paperclip-btn", "tool-activity-btn"):
        m = re.search(r'<div class="list-item" id="%s"[^>]*>' % re.escape(tool_id), _INDEX)
        assert m, f"could not find opening tag for {tool_id!r}"
        assert "display:none" in m.group(0), f"{tool_id!r} should still default to display:none"


def test_know_section_has_an_appearance_visibility_toggle():
    assert 'data-ui-key="know-section"' in _INDEX, (
        "expected an Appearance-tab vis-row checkbox for know-section in index.html"
    )
    assert "'know-section'" in _APP_JS, (
        "expected a 'know-section' entry in UI_VIS_MAP in static/app.js"
    )
