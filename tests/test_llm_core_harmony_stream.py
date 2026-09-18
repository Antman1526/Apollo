"""gpt-oss harmony markup arriving as plain `content` deltas (mlx_lm.server has
no harmony parser) must stream as thinking / answer / native tool calls, and
the hook must leave every other stream untouched."""
import asyncio
import json

from src import llm_core


class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *args, **kwargs):
        return _FakeStreamCtx(self._lines)


def _sse(*contents, done=True):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) for c in contents]
    return lines + (["data: [DONE]"] if done else [])


def _run(lines, monkeypatch, model="gpt-oss-20b"):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))

    async def _go():
        out = []
        async for chunk in llm_core.stream_llm("http://mlx:8080/v1/chat/completions", model,
                                               [{"role": "user", "content": "hi"}]):
            out.append(chunk)
        return out

    events = []
    for chunk in asyncio.run(_go()):
        for raw in chunk.splitlines():
            if raw.startswith("data: {"):
                events.append(json.loads(raw[6:]))
    return events


def _text(events, thinking=False):
    return "".join(e["delta"] for e in events
                   if "delta" in e and bool(e.get("thinking")) is thinking)


def test_harmony_answer_streams_as_thinking_then_content(monkeypatch):
    events = _run(_sse("<|channel|>", "analysis", "<|message|>", "User wants PONG.", "<|end|>",
                       "<|start|>", "assistant", "<|channel|>", "final", "<|message|>", "PO", "NG"),
                  monkeypatch)
    assert _text(events, thinking=True) == "User wants PONG."
    assert _text(events) == "PONG"
    assert not any("<|" in e.get("delta", "") for e in events)
    assert not any(e.get("type") == "tool_calls" for e in events)


def test_harmony_tool_call_becomes_native_tool_calls_event(monkeypatch):
    events = _run(_sse("<|channel|>analysis<|message|>Call the tool.<|end|>",
                       "<|start|>assistant<|channel|>commentary to=functions.python",
                       "<|constrain|>json<|message|>", '{"code":', '"print(1)"}'),
                  monkeypatch)
    assert _text(events) == ""
    [tc] = [e for e in events if e.get("type") == "tool_calls"]
    [call] = tc["calls"]
    assert call["name"] == "python"
    assert call["arguments"] == '{"code":"print(1)"}'
    assert call["id"].startswith("call_")


def test_harmony_without_done_marker_still_flushes(monkeypatch):
    events = _run(_sse("<|channel|>final<|message|>Hello", done=False), monkeypatch)
    assert _text(events) == "Hello"


def test_plain_stream_is_untouched_even_when_it_starts_with_a_bracket(monkeypatch):
    events = _run(_sse("<", "b>bold</b>", " done"), monkeypatch, model="llama-3.1-8b")
    assert _text(events) == "<b>bold</b> done"
    assert _text(events, thinking=True) == ""


def test_plain_stream_with_think_tag_is_untouched(monkeypatch):
    events = _run(_sse("<think>", "hm", "</think>", "answer"), monkeypatch, model="qwen3-8b")
    assert _text(events) == "<think>hm</think>answer"


def test_non_streaming_message_text_unwraps_harmony():
    msg = {"content": "<|channel|>analysis<|message|>think<|end|><|start|>assistant"
                      "<|channel|>final<|message|>PONG"}
    assert llm_core._message_text(msg) == "PONG"
    assert llm_core._message_text({"content": "", "reasoning": "only reasoning"}) == "only reasoning"
    assert llm_core._message_text({"content": "plain"}) == "plain"


def test_finish_reason_is_forwarded_as_an_event(monkeypatch):
    line = "data: " + json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]})
    events = _run([line, "data: [DONE]"], monkeypatch, model="qwen3-8b")
    assert {"type": "finish_reason", "reason": "length"} in events
    assert _text(events) == "x"
