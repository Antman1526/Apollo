"""Decide whether a chat message needs live web results (web_access=auto).

Two stages:
1. heuristic_decision(message) — instant regex pass: 'yes' | 'no' | 'ambiguous'
2. decide_use_web / resolve_web_access — async tie-break + tri-state mode mapping.

resolve_web_access(web_access, chat_mode, message, use_web, allow_web_search)
    Maps tri-state web_access ('off'|'auto'|'always') onto the chat pipeline's
    legacy use_web / allow_web_search flags plus a decision label.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Explicit asks — always search.
_FORCE_RE = re.compile(
    r"\b(search( the web)?( for)?|look up|google|web search)\b", re.I)

# Freshness / volatile-fact signals.
_RECENCY_RE = re.compile(
    r"\b(today|tonight|yesterday|this (week|month|year)|latest|current(ly)?|"
    r"breaking|news|headlines?|price|stock|weather|forecast|score|schedule[d]?|"
    r"release (date|notes)|just (released|announced)|right now|upcoming|next "
    r"(launch|release|election|game|match))\b", re.I)

# Self-contained work — the answer is in the message or the model's weights.
_NO_WEB_RE = re.compile(
    r"\b(refactor|rewrite|fix (this|my|the)|debug|translate|"
    r"write( me)? a (poem|story|song|haiku|letter|script)|"
    r"summari[sz]e (this|the following)|explain (this|the following))\b", re.I)

_URL_RE = re.compile(r"https?://", re.I)

_QUESTION_WORDS = ("who", "what", "when", "where", "which", "how much", "how many")


def heuristic_decision(message: Optional[str]) -> str:
    """Classify a message: 'yes' (search), 'no' (skip), 'ambiguous'."""
    msg = (message or "").strip()
    if not msg:
        return "no"
    if "```" in msg or len(msg) > 4000:
        return "no"  # pasted code/content — answer from what's provided
    if _URL_RE.search(msg):
        return "no"  # embedded URLs are auto-fetched by chat_processor
    if _NO_WEB_RE.search(msg):
        return "no"
    if _FORCE_RE.search(msg):
        return "yes"
    if _RECENCY_RE.search(msg):
        return "yes"
    lower = msg.lower()
    if msg.rstrip().endswith("?") and any(lower.startswith(w) or f" {w} " in lower
                                          for w in _QUESTION_WORDS):
        return "ambiguous"
    return "no"


async def _ask_utility_model(message: str) -> Optional[bool]:
    """One-token YES/NO from the utility model. None on any failure.

    Only called when utility_endpoint_id is explicitly set — that endpoint is
    always-on (e.g. Ollama) and separate from the single warm llama.cpp slot,
    so this never forces a local model swap.
    """
    try:
        import httpx
        from src.endpoint_resolver import resolve_endpoint

        # resolve_endpoint returns (chat_url, model, headers) — all already
        # processed: chat_url already has /chat/completions (or /api/chat for
        # Ollama, /v1/messages for Anthropic), headers already contain auth.
        url, model, headers = resolve_endpoint("utility")
        if not url or not model:
            return None

        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": ("Does answering this message require up-to-date "
                            "information from the public web (news, prices, "
                            "schedules, recent events, current facts)? "
                            "Answer with exactly YES or NO.\n\n"
                            f"Message: {message[:500]}"),
            }],
            "max_tokens": 3,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.post(url, json=payload, headers=headers or {})
            r.raise_for_status()
            text = (r.json()["choices"][0]["message"]["content"] or "").strip().upper()
        if text.startswith("YES"):
            return True
        if text.startswith("NO"):
            return False
        return None
    except Exception as e:
        logger.debug("utility web-decider call failed: %s", e)
        return None


async def decide_use_web(message: str) -> bool:
    """Return True if the message likely needs a live web search.

    Heuristics give a clear yes/no when possible.  For ambiguous questions
    (fact-seeking, named-entity lookups) the utility model is consulted — but
    ONLY when ``utility_endpoint_id`` is explicitly set so we never force a
    local model swap on the single warm llama.cpp slot.
    """
    verdict = heuristic_decision(message)
    if verdict == "yes":
        return True
    if verdict == "no":
        return False
    # ambiguous — tie-break with the utility model when one is configured
    from src.settings import load_settings
    if (load_settings().get("utility_endpoint_id") or "").strip():
        answer = await _ask_utility_model(message)
        if answer is not None:
            return answer
    return False  # conservative default: no extra latency/noise


async def resolve_web_access(
    web_access: Optional[str],
    chat_mode: str,
    message: Optional[str],
    use_web,
    allow_web_search,
) -> Tuple:
    """Map the tri-state web_access onto (use_web, allow_web_search, decision).

    Args:
        web_access: 'off' | 'auto' | 'always' | None (fall back to settings).
        chat_mode:  'chat' or 'agent'.
        message:    The user's message (used by the decider in auto mode).
        use_web:    Legacy flag from the chat pipeline (passed through when
                    mode is manual).
        allow_web_search: Legacy flag from the chat pipeline (likewise).

    Returns:
        (use_web, allow_web_search, decision) where decision is None when
        legacy manual behaviour applies (flags untouched), or one of:
        'off', 'always', 'auto-tools', 'auto-search', 'auto-skip'.
    """
    mode = (web_access or "").strip().lower()
    if mode not in ("off", "auto", "always"):
        from src.settings import load_settings
        cfg = (load_settings().get("web_access_mode") or "manual").strip().lower()
        if cfg not in ("auto", "always"):
            # Manual / unrecognised — leave legacy flags untouched.
            return use_web, allow_web_search, None
        mode = cfg

    if mode == "off":
        return False, "false", "off"

    if mode == "always":
        if chat_mode == "agent":
            return use_web, "true", "always"
        return True, allow_web_search, "always"

    # auto
    if chat_mode == "agent":
        # Tools are available; the model decides per call. No forced pre-search.
        return use_web, "true", "auto-tools"

    needed = await decide_use_web(message or "")
    return needed, allow_web_search, ("auto-search" if needed else "auto-skip")
