"""Persistent python_session kernel: state persistence, isolation, crash
containment, timeout recovery, and LRU eviction — all exercised against the
real worker subprocess (no mocking; this is exactly what the agent tool
calls)."""
import asyncio

import pytest

from services.python_kernel import PythonSessionManager, MAX_KERNELS


def _run(coro):
    return asyncio.run(coro)


def test_state_persists_within_a_session():
    async def go():
        m = PythonSessionManager()
        try:
            r1 = await m.run("s1", "x = 42")
            assert r1["exit_code"] == 0
            r2 = await m.run("s1", "print(x * 2)")
            assert r2 == {"output": "84", "exit_code": 0}
        finally:
            await m.stop_all()
    _run(go())


def test_state_isolated_across_sessions():
    async def go():
        m = PythonSessionManager()
        try:
            await m.run("s1", "x = 1")
            r = await m.run("s2", "print(x)")
            assert r["exit_code"] == 1
            assert "NameError" in r["output"]
        finally:
            await m.stop_all()
    _run(go())


def test_exception_does_not_kill_the_kernel():
    async def go():
        m = PythonSessionManager()
        try:
            await m.run("s1", "x = 42")
            crash = await m.run("s1", "1 / 0")
            assert crash["exit_code"] == 1
            assert "ZeroDivisionError" in crash["output"]
            survives = await m.run("s1", "print(x)")
            assert survives == {"output": "42", "exit_code": 0}
        finally:
            await m.stop_all()
    _run(go())


def test_timeout_kills_and_a_fresh_kernel_recovers():
    async def go():
        m = PythonSessionManager()
        try:
            r = await m.run("s1", "import time; time.sleep(30)", timeout=1.0)
            assert r["exit_code"] == 124
            assert m.active_count() == 0
            fresh = await m.run("s1", "print('ok')")
            assert fresh == {"output": "ok", "exit_code": 0}
        finally:
            await m.stop_all()
    _run(go())


def test_captures_stdout_and_stderr():
    async def go():
        m = PythonSessionManager()
        try:
            r = await m.run("s1", "import sys\nprint('out')\nprint('err', file=sys.stderr)")
            assert "out" in r["output"] and "STDERR: err" in r["output"]
            assert r["exit_code"] == 0
        finally:
            await m.stop_all()
    _run(go())


def test_syntax_error_reported_cleanly():
    async def go():
        m = PythonSessionManager()
        try:
            r = await m.run("s1", "def broken(:\n  pass")
            assert r["exit_code"] == 1
            assert "SyntaxError" in r["output"]
        finally:
            await m.stop_all()
    _run(go())


def test_stop_session_removes_it():
    async def go():
        m = PythonSessionManager()
        try:
            await m.run("s1", "x = 1")
            assert m.active_count() == 1
            removed = await m.stop_session("s1")
            assert removed is True
            assert m.active_count() == 0
            assert await m.stop_session("s1") is False  # already gone
        finally:
            await m.stop_all()
    _run(go())


def test_lru_eviction_bounds_concurrent_kernels():
    async def go():
        m = PythonSessionManager()
        try:
            for i in range(MAX_KERNELS + 2):
                await m.run(f"sess-{i}", "x = 1")
            assert m.active_count() <= MAX_KERNELS
        finally:
            await m.stop_all()
    _run(go())


def test_output_truncated_at_cap():
    async def go():
        m = PythonSessionManager()
        try:
            r = await m.run("s1", "print('x' * 50000)")
            assert len(r["output"]) < 25_000
            assert "truncated" in r["output"]
        finally:
            await m.stop_all()
    _run(go())


def test_run_never_raises_on_manager_error(monkeypatch):
    """Even if kernel bookkeeping throws internally, run() must return a
    clean error dict — this is the tool's own crash-containment contract."""
    async def go():
        m = PythonSessionManager()
        async def boom():
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(m, "_evict_lru_if_full", boom)
        r = await m.run("s1", "x = 1")
        assert r["exit_code"] == 1
        assert "internal error" in r["error"]
    _run(go())


# ── Dispatch integration: the python_session TOOL, not just the manager ──

def test_python_session_tool_dispatch_uses_session_id():
    """execute_tool_block must route python_session through the session-keyed
    kernel manager (direct dispatch, not the MCP tool map — the MCP path
    doesn't carry session_id)."""
    from types import SimpleNamespace
    import src.tool_execution as te

    async def go():
        block = SimpleNamespace(tool_type="python_session", content="print('hi from tool')")
        desc, result = await te.execute_tool_block(block, session_id="sess-A", owner="admin")
        assert result["exit_code"] == 0
        assert "hi from tool" in result["output"]
        from services.python_kernel import get_manager
        await get_manager().stop_all()
    asyncio.run(go())


def test_python_session_requires_session_id():
    from types import SimpleNamespace
    import src.tool_execution as te

    async def go():
        block = SimpleNamespace(tool_type="python_session", content="print(1)")
        desc, result = await te.execute_tool_block(block, session_id=None, owner="admin")
        assert result["exit_code"] == 1
        assert "active chat session" in result["error"]
    asyncio.run(go())


def test_python_session_blocked_for_non_admin(monkeypatch):
    from types import SimpleNamespace
    import src.tool_execution as te

    monkeypatch.setattr(te, "owner_is_admin_or_single_user", lambda owner: False)

    async def go():
        block = SimpleNamespace(tool_type="python_session", content="print(1)")
        desc, result = await te.execute_tool_block(block, session_id="sess-B", owner="regular_user")
        assert result["exit_code"] == 1
        assert "admin" in desc.lower() or "admin" in result["error"].lower()
    asyncio.run(go())


def test_python_session_in_mutating_tools_for_autonomy_dial():
    import src.tool_execution as te
    assert "python_session" in te._MUTATING_TOOLS
