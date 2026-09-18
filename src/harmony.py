"""Incremental parser for gpt-oss "harmony" output that a runtime passed through raw.

llama.cpp parses this format itself; mlx_lm.server does not, so its content
stream looks like::

    <|channel|>analysis<|message|>thinking…<|end|><|start|>assistant
    <|channel|>final<|message|>the answer

and a tool call is::

    <|channel|>commentary to=functions.get_weather <|constrain|>json<|message|>{"city":"Paris"}<|call|>

`HarmonyStream.feed` turns content deltas into ("thinking", text) /
("content", text) events and collects tool calls; the special tokens never
reach the user.
"""
from __future__ import annotations

import re

_MARKER_RE = re.compile(r"<\|(channel|message|end|start|call|return)\|>")
_BODY_END = {"end", "call", "return"}


def looks_like_harmony(text: str) -> bool:
    """True when `text` is (the start of) a harmony-formatted reply."""
    head = text.lstrip()
    return head.startswith("<|channel|>") or head.startswith("<|start|>")


def could_become_harmony(text: str) -> bool:
    """True while `text` is still a prefix of a harmony opener (keep buffering)."""
    head = text.lstrip()
    return bool(head) and ("<|channel|>".startswith(head) or "<|start|>".startswith(head))


class HarmonyStream:
    def __init__(self) -> None:
        self._buf = ""
        self._state = "idle"  # idle | header | body | role
        self._header = ""
        self._kind = "content"  # thinking | content | tool
        self._tool_name = ""
        self._tool_args = ""
        self.tool_calls: list[dict] = []

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []
        while self._buf:
            m = _MARKER_RE.search(self._buf)
            if m is None:
                # Hold back a trailing partial marker; emit the rest.
                cut = self._buf.rfind("<|")
                if cut == -1 and self._buf.endswith("<"):
                    cut = len(self._buf) - 1
                if cut != -1 and "|>" not in self._buf[cut:]:
                    text, self._buf = self._buf[:cut], self._buf[cut:]
                else:
                    text, self._buf = self._buf, ""
                self._text(text, out)
                break
            self._text(self._buf[:m.start()], out)
            self._buf = self._buf[m.end():]
            self._marker(m.group(1), out)
        return out

    def finish(self) -> list[tuple[str, str]]:
        """Flush at end of stream; a tool body without <|call|> still counts."""
        out: list[tuple[str, str]] = []
        self._text(self._buf, out)
        self._buf = ""
        self._end_body()
        return out

    # -- internals ----------------------------------------------------------
    def _text(self, text: str, out: list) -> None:
        if not text:
            return
        if self._state == "header":
            self._header += text
        elif self._state == "role":
            pass  # "assistant" between <|start|> and <|channel|>
        elif self._state == "body" and self._kind == "tool":
            self._tool_args += text
        elif self._state == "body" and self._kind == "thinking":
            out.append(("thinking", text))
        else:
            out.append(("content", text))

    def _marker(self, name: str, out: list) -> None:
        if name == "channel":
            self._end_body()
            self._state, self._header = "header", ""
        elif name == "message":
            self._open_body()
        elif name in _BODY_END:
            self._end_body()
            self._state = "idle"
        elif name == "start":
            self._end_body()
            self._state = "role"

    def _open_body(self) -> None:
        header = self._header.strip()
        self._state = "body"
        if header.startswith("analysis"):
            self._kind = "thinking"
        elif header.startswith("commentary") and "to=functions." in header:
            self._kind = "tool"
            rest = header.split("to=functions.", 1)[1]
            # Name runs to whitespace or the next marker: gpt-oss writes both
            # "to=functions.python <|constrain|>json" and "to=functions.python<|constrain|>json".
            m = re.match(r"[A-Za-z0-9_.\-]+", rest)
            self._tool_name = m.group(0) if m else rest.split()[0]
            self._tool_args = ""
        else:  # final, or a commentary preamble meant for the user
            self._kind = "content"

    def _end_body(self) -> None:
        if self._state == "body" and self._kind == "tool":
            self.tool_calls.append({"name": self._tool_name, "arguments": self._tool_args.strip()})
        self._state = "idle"
        self._kind = "content"


def parse_harmony(text: str) -> tuple[str, str, list[dict]]:
    """Whole-response helper: (content, thinking, tool_calls)."""
    p = HarmonyStream()
    events = p.feed(text) + p.finish()
    content = "".join(t for k, t in events if k == "content")
    thinking = "".join(t for k, t in events if k == "thinking")
    return content, thinking, p.tool_calls
