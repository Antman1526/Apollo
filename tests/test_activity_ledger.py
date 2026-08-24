"""Activity ledger: record, list/search, undo, prune, and the tool-exec hook."""
import asyncio
import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


def _evict_module_stubs():
    """Drop MagicMock module stubs other suites leave in sys.modules.

    test_unknown_tool_calls (and friends) mock sqlalchemy/core.database at
    module scope without restoring them. Pytest imports all test modules at
    collection time, so the pollution lands before our fixtures run — evict
    at fixture runtime so the real modules re-import regardless of order.
    """
    for mod in list(sys.modules):
        if (mod in ("core.database", "services.activity_ledger")
                or mod.startswith("sqlalchemy")) and isinstance(sys.modules[mod], MagicMock):
            sys.modules.pop(mod)


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "bcrypt", "cryptography"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+bcrypt+cryptography installed"
)


@pytest.fixture
def ledger(monkeypatch):
    _evict_module_stubs()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base
    import services.activity_ledger as al

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(al, "SessionLocal", TestSessionLocal)
    settings = {"activity_ledger_enabled": True, "activity_ledger_max_events": 10000}
    monkeypatch.setattr(al, "get_setting", lambda k, d=None: settings.get(k, d))
    return al, settings


def test_record_and_list(ledger):
    al, _ = ledger
    eid = al.record_event(
        tool="bash", summary="bash: ls", input_text="ls -la",
        result={"output": "total 8", "exit_code": 0},
        session_id="s1", owner="antman", duration_ms=42,
    )
    assert eid
    events = al.list_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["tool"] == "bash"
    assert ev["exit_code"] == 0
    assert ev["session_id"] == "s1"
    assert not ev["undoable"]


def test_search_and_filters(ledger):
    al, _ = ledger
    al.record_event(tool="bash", input_text="grep needle file.txt", result={"output": "", "exit_code": 0})
    al.record_event(tool="web_fetch", input_text="https://example.com", result={"output": "ok", "exit_code": 0})
    assert len(al.list_events(q="needle")) == 1
    assert len(al.list_events(tool="web_fetch")) == 1
    assert len(al.list_events(tool="bash", q="example.com")) == 0


def test_skip_tools_and_disabled(ledger):
    al, settings = ledger
    assert al.record_event(tool="read_file", input_text="/x") is None
    settings["activity_ledger_enabled"] = False
    assert al.record_event(tool="bash", input_text="ls") is None
    assert al.list_events() == []


def test_undo_restores_previous_content(ledger, tmp_path):
    al, _ = ledger
    target = tmp_path / "f.txt"
    target.write_text("original")
    before = al.capture_before(str(target))
    target.write_text("overwritten")
    eid = al.record_event(
        tool="write_file", input_text=f"{target}\noverwritten",
        result={"output": "wrote", "exit_code": 0},
        path=str(target), before=before,
    )
    assert al.list_events()[0]["undoable"]
    res = al.undo_event(eid)
    assert res["ok"]
    assert target.read_text() == "original"
    # marked undone; second undo refuses
    assert not al.undo_event(eid)["ok"]
    # an "undo" event was itself recorded
    assert any(e["tool"] == "undo" for e in al.list_events())


def test_undo_deletes_file_that_did_not_exist(ledger, tmp_path):
    al, _ = ledger
    target = tmp_path / "new.txt"
    before = al.capture_before(str(target))
    target.write_text("created by agent")
    eid = al.record_event(
        tool="write_file", input_text=f"{target}\ncreated",
        result={"output": "wrote", "exit_code": 0},
        path=str(target), before=before,
    )
    assert al.undo_event(eid)["ok"]
    assert not target.exists()


def test_undo_rejects_non_undoable(ledger):
    al, _ = ledger
    eid = al.record_event(tool="bash", input_text="ls", result={"output": "", "exit_code": 0})
    assert not al.undo_event(eid)["ok"]
    assert not al.undo_event("nope")["ok"]


def test_prune_keeps_table_bounded(ledger):
    al, settings = ledger
    settings["activity_ledger_max_events"] = 5
    for i in range(8):
        al.record_event(tool="bash", input_text=f"cmd {i}", result={"exit_code": 0})
    assert len(al.list_events(limit=100)) <= 5


def test_execute_tool_block_hook_records(monkeypatch, tmp_path):
    _evict_module_stubs()
    import src.tool_execution as te
    import services.activity_ledger as al

    recorded = {}
    async def fake_inner(block, **kw):
        return "bash: ok", {"output": "hi", "exit_code": 0}
    monkeypatch.setattr(te, "_execute_tool_block_inner", fake_inner)
    monkeypatch.setattr(al, "record_event", lambda **kw: recorded.update(kw) or "id1")

    class Block:
        tool_type = "bash"
        content = "echo hi"

    desc, result = asyncio.run(
        te.execute_tool_block(Block(), session_id="s9", owner="antman")
    )
    assert desc == "bash: ok"
    assert recorded["tool"] == "bash"
    assert recorded["session_id"] == "s9"
    assert recorded["result"]["exit_code"] == 0
    assert recorded["duration_ms"] >= 0


def test_execute_tool_block_hook_captures_write_before(monkeypatch, tmp_path):
    _evict_module_stubs()
    import src.tool_execution as te
    import services.activity_ledger as al

    target = tmp_path / "doc.txt"
    target.write_text("old body")
    recorded = {}

    async def fake_inner(block, **kw):
        return "write_file: ok", {"output": "wrote", "exit_code": 0}
    monkeypatch.setattr(te, "_execute_tool_block_inner", fake_inner)
    monkeypatch.setattr(te, "_resolve_tool_path", lambda p: p)
    monkeypatch.setattr(al, "record_event", lambda **kw: recorded.update(kw) or "id1")

    class Block:
        tool_type = "write_file"
        content = f"{target}\nnew body"

    asyncio.run(te.execute_tool_block(Block()))
    assert recorded["path"] == str(target)
    assert recorded["before"]["before_existed"] is True
    assert recorded["before"]["before_content"] == "old body"


def test_ledger_failure_never_breaks_tool(monkeypatch):
    _evict_module_stubs()
    import src.tool_execution as te
    import services.activity_ledger as al

    async def fake_inner(block, **kw):
        return "bash: ok", {"output": "hi", "exit_code": 0}
    monkeypatch.setattr(te, "_execute_tool_block_inner", fake_inner)
    def boom(**kw):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(al, "record_event", boom)

    class Block:
        tool_type = "bash"
        content = "echo hi"

    desc, result = asyncio.run(te.execute_tool_block(Block()))
    assert result["exit_code"] == 0  # tool result unaffected


def test_cap_pinned():
    from src.chat_processor import _cap_pinned
    mems = [{"text": str(i), "timestamp": i} for i in range(20)]
    capped = _cap_pinned(mems, 5)
    assert len(capped) == 5
    assert capped[0]["timestamp"] == 19  # newest first
    assert _cap_pinned(mems, 0) == mems  # 0 = no cap
    assert _cap_pinned(mems[:3], 5) == mems[:3]


def test_failed_write_gets_no_undo_snapshot(monkeypatch, tmp_path):
    _evict_module_stubs()
    import src.tool_execution as te
    import services.activity_ledger as al

    target = tmp_path / "doc.txt"
    target.write_text("old body")
    recorded = {}

    async def fake_inner(block, **kw):
        return "write_file: error", {"error": "permission denied", "exit_code": 1}
    monkeypatch.setattr(te, "_execute_tool_block_inner", fake_inner)
    monkeypatch.setattr(te, "_resolve_tool_path", lambda p: p)
    monkeypatch.setattr(al, "record_event", lambda **kw: recorded.update(kw) or "id1")

    class Block:
        tool_type = "write_file"
        content = f"{target}\nnew body"

    asyncio.run(te.execute_tool_block(Block()))
    assert recorded["before"] is None  # failed write → no undo affordance
    assert recorded["path"] == str(target)  # path still audited


def test_undo_session_rolls_back_newest_first(ledger, tmp_path):
    al, _ = ledger
    target = tmp_path / "f.txt"
    # Write twice in one session: original -> v1 -> v2
    target.write_text("original")
    b1 = al.capture_before(str(target))
    target.write_text("v1")
    al.record_event(tool="write_file", input_text=f"{target}\nv1",
                    result={"exit_code": 0}, path=str(target), before=b1,
                    session_id="sess1")
    b2 = al.capture_before(str(target))
    target.write_text("v2")
    al.record_event(tool="write_file", input_text=f"{target}\nv2",
                    result={"exit_code": 0}, path=str(target), before=b2,
                    session_id="sess1")
    # unrelated session untouched
    other = tmp_path / "other.txt"
    b3 = al.capture_before(str(other))
    other.write_text("other")
    al.record_event(tool="write_file", input_text=f"{other}\nother",
                    result={"exit_code": 0}, path=str(other), before=b3,
                    session_id="sess2")

    res = al.undo_session("sess1")
    assert res["ok"] and res["undone"] == 2 and not res["failed"]
    assert target.read_text() == "original"
    assert other.exists()  # sess2 untouched

    assert not al.undo_session("sess1")["ok"]   # nothing left
    assert not al.undo_session("")["ok"]


def test_observe_mode_blocks_mutating_tools(monkeypatch):
    _evict_module_stubs()
    import src.tool_execution as te
    import services.activity_ledger as al

    recorded = []
    called = {"inner": False}
    async def fake_inner(block, **kw):
        called["inner"] = True
        return "bash: ok", {"output": "hi", "exit_code": 0}
    monkeypatch.setattr(te, "_execute_tool_block_inner", fake_inner)
    monkeypatch.setattr(al, "record_event", lambda **kw: recorded.append(kw) or "id")
    monkeypatch.setattr("src.settings.get_setting",
                        lambda k, d=None: "observe" if k == "agent_autonomy" else d)

    class Bash:
        tool_type = "bash"
        content = "rm -rf /tmp/x"

    desc, result = asyncio.run(te.execute_tool_block(Bash()))
    assert "BLOCKED" in desc and result["exit_code"] == 1
    assert not called["inner"]           # never dispatched
    assert recorded and recorded[0]["tool"] == "bash"  # refusal audited

    class Search:
        tool_type = "web_search"
        content = "apollo"

    desc2, result2 = asyncio.run(te.execute_tool_block(Search()))
    assert result2["exit_code"] == 0     # read-only tools unaffected
