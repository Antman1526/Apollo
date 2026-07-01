"""Distill a chat transcript into atomic, durable facts (memories).

Pure: the LLM is injected as `llm_caller(messages) -> str`, so this is
unit-testable without a model or DB.
"""
import re

_SYSTEM = (
    "You extract durable, atomic facts from a conversation to store in a personal "
    "knowledge base. Output ONE fact per line, each a short standalone statement "
    "(no first-person, no 'the user asked'). Capture preferences, decisions, "
    "identity, projects, and stable facts. Skip chit-chat, transient context, and "
    "anything not worth remembering later. If there is nothing durable, output NONE."
)

_SKIP = {"none", "(none)", "n/a", "(no durable facts)", "no durable facts"}


def build_distill_prompt(transcript: str) -> list:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nDurable facts:"},
    ]


def parse_facts(llm_text: str) -> list:
    out = []
    for line in (llm_text or "").splitlines():
        s = line.strip()
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", s)  # strip bullet/number markers
        s = s.strip()
        if not s or s.lower() in _SKIP:
            continue
        out.append(s)
    return out


def distill_transcript(transcript: str, llm_caller) -> list:
    if not (transcript or "").strip():
        return []
    text = llm_caller(build_distill_prompt(transcript))
    return parse_facts(text)
