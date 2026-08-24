"""Regression: a tool that raises unexpectedly is converted into a normal
error result the model can react to, instead of killing the SSE stream."""
import ast
import pathlib


def test_tool_await_is_guarded():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("src/agent_loop.py").read_text()
    # The `await _tool_task` must sit inside a try/except that produces an
    # error dict rather than propagating. Assert both the guard and the
    # fallback result shape exist near the await.
    assert "desc, result = await _tool_task" in src
    idx = src.index("desc, result = await _tool_task")
    window = src[max(0, idx - 400):idx + 400]
    assert "try:" in window, "await _tool_task must be inside a try block"
    assert "except Exception" in window, "must catch tool crashes"
    assert '"exit_code": 1' in window, "crash must yield an error result dict"
    # Sanity: the file still parses.
    ast.parse(src)
