"""gpt-oss harmony markup passed through raw by mlx_lm.server."""
from src.harmony import HarmonyStream, could_become_harmony, looks_like_harmony, parse_harmony

ANSWER = ('<|channel|>analysis<|message|>User says PONG. So reply PONG.<|end|>'
          '<|start|>assistant<|channel|>final<|message|>PONG')
TOOL = ('<|channel|>analysis<|message|>We need to call get_weather.<|end|>'
        '<|start|>assistant<|channel|>commentary to=functions.get_weather '
        '<|constrain|>json<|message|>{"city":"Paris"}')


def test_whole_answer_splits_thinking_and_content():
    content, thinking, calls = parse_harmony(ANSWER)
    assert content == "PONG"
    assert thinking == "User says PONG. So reply PONG."
    assert calls == []


def test_tool_call_without_call_marker_is_collected_on_finish():
    content, thinking, calls = parse_harmony(TOOL)
    assert content == ""
    assert calls == [{"name": "get_weather", "arguments": '{"city":"Paris"}'}]


def test_call_marker_ends_tool_body():
    content, _, calls = parse_harmony(TOOL + "<|call|>")
    assert calls == [{"name": "get_weather", "arguments": '{"city":"Paris"}'}]
    assert content == ""


def test_streaming_token_by_token_matches_whole_parse():
    p = HarmonyStream()
    events = []
    for i in range(len(ANSWER)):  # one character at a time: markers split everywhere
        events += p.feed(ANSWER[i])
    events += p.finish()
    assert "".join(t for k, t in events if k == "content") == "PONG"
    assert "".join(t for k, t in events if k == "thinking") == "User says PONG. So reply PONG."
    assert all(k in ("content", "thinking") for k, _ in events)
    assert not any("<|" in t for _, t in events)


def test_commentary_preamble_is_user_visible():
    content, _, calls = parse_harmony(
        '<|channel|>commentary<|message|>Checking the weather now.<|end|>'
        '<|start|>assistant<|channel|>commentary to=functions.get_weather<|message|>{}<|call|>')
    assert content == "Checking the weather now."
    assert calls[0]["name"] == "get_weather"


def test_detection():
    assert looks_like_harmony("<|channel|>analysis")
    assert looks_like_harmony("\n<|start|>assistant")
    assert not looks_like_harmony("Hello")
    assert could_become_harmony("<|ch")
    assert could_become_harmony("<|")
    assert not could_become_harmony("<|x")
    assert not could_become_harmony("Hi")


def test_tool_name_stops_at_constrain_marker_without_space():
    _, _, calls = parse_harmony(
        '<|channel|>commentary to=functions.python<|constrain|>json<|message|>{"code":"print(1)"}')
    assert calls == [{"name": "python", "arguments": '{"code":"print(1)"}'}]
