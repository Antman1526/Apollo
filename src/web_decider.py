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

# Explicit asks — always search (checked before URL so intent wins).
# 'google' requires a query-like follow-up or standalone verb usage
# ("google it", "google for X") to avoid matching "google docs API how to".
_FORCE_RE = re.compile(
    r"\b(search( the web)?( for)?|look up|web search|"
    r"google\s+(it|for\b|this\b)|google\s+\w+\s+(search|results?))\b",
    re.I,
)

# Strong recency signals — these indicate live-web need on their own.
# Includes unambiguous freshness words and time anchors.
_STRONG_RECENCY_RE = re.compile(
    r"\b(today|tonight|yesterday|this (week|month|year)|latest|breaking|"
    r"just (released|announced)|right now|upcoming|"
    r"next (launch|release|election|game|match)|"
    r"release (date|notes)|weather\b)\b",
    re.I,
)

# Weak recency nouns — only count as a freshness signal when paired with a
# question shape (ends with '?' or starts with a question word) OR with a
# strong freshness co-signal like "current", "today", "latest", "now".
# This prevents coding-vocabulary false positives:
#   "update the price field in my schema" → price alone is NOT enough
#   "current price of AMD stock"          → price + "current" co-signal → yes
_WEAK_RECENCY_RE = re.compile(
    r"\b(price|stock|score|schedule[d]?|forecast|news|headlines?)\b", re.I
)

# Co-signals that make a weak recency noun count as a freshness hit.
_FRESHNESS_CO_RE = re.compile(
    r"\b(current(ly)?|today|tonight|yesterday|latest|right now|"
    r"this (week|month|year)|just|upcoming|breaking|live)\b",
    re.I,
)

# Self-contained work — the answer is in the message or the model's weights.
_NO_WEB_RE = re.compile(
    r"\b(refactor|rewrite|fix (this|my|the)|debug|translate|"
    r"write( me)? a (poem|story|song|haiku|letter|script)|"
    r"summari[sz]e (this|the following)|explain (this|the following))\b", re.I)

_URL_RE = re.compile(r"https?://", re.I)

_QUESTION_WORDS = ("who", "what", "when", "where", "which", "how much", "how many")


def _is_question_shaped(msg: str) -> bool:
    """Return True if msg ends with '?' and starts with (or contains) a question word."""
    lower = msg.lower()
    return msg.rstrip().endswith("?") and any(
        lower.startswith(w) or f" {w} " in lower for w in _QUESTION_WORDS
    )


def _has_recency_signal(msg: str) -> bool:
    """Return True if msg has a strong recency word or a weak noun with co-signal/question."""
    if _STRONG_RECENCY_RE.search(msg):
        return True
    if _WEAK_RECENCY_RE.search(msg):
        # Weak noun counts only when paired with a freshness co-signal or question shape.
        return bool(_FRESHNESS_CO_RE.search(msg)) or _is_question_shaped(msg)
    return False


def heuristic_decision(message: Optional[str]) -> str:
    """Classify a message: 'yes' (search), 'no' (skip), 'ambiguous'.

    Precedence (first match wins):
    1. empty / code-paste / long → no
    2. NO_WEB verbs (refactor, fix this, …) → no
    3. FORCE explicit search intent (search for, look up, …) → yes
       (checked before URL so "search for https://…" still searches)
    4. URL present:
       a. URL + recency signal → ambiguous  (let tie-breaker decide)
       b. URL alone            → no         (chat_processor auto-fetches)
    5. Strong/weak recency signal (see _has_recency_signal) → yes
    6. Question-shaped ambiguous
    7. default → no
    """
    msg = (message or "").strip()
    if not msg:
        return "no"
    if "```" in msg or len(msg) > 4000:
        return "no"  # pasted code/content — answer from what's provided
    if _NO_WEB_RE.search(msg):
        return "no"
    if _FORCE_RE.search(msg):
        return "yes"
    if _URL_RE.search(msg):
        # URL + explicit recency → ambiguous (may need both fetch + search)
        if _has_recency_signal(msg):
            return "ambiguous"
        return "no"  # pure URL: auto-fetched by chat_processor
    if _has_recency_signal(msg):
        return "yes"
    if _is_question_shaped(msg):
        return "ambiguous"
    return "no"


def _extract_reply_text(data: dict) -> str:
    """Pull the assistant text out of OpenAI / Ollama / Anthropic chat responses."""
    try:
        choices = data.get("choices")
        if choices:  # OpenAI-compatible
            return choices[0].get("message", {}).get("content") or ""
        msg = data.get("message")
        if isinstance(msg, dict):  # Ollama native /api/chat
            return msg.get("content") or ""
        content = data.get("content")
        if isinstance(content, list):  # Anthropic /v1/messages
            return "".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    except Exception:
        pass
    return ""


async def _ask_utility_model(message: str) -> Optional[bool]:
    """One-token YES/NO from the utility model. None on any failure.

    Refuses to run unless ``utility_endpoint_id`` is explicitly set in
    settings.  This is a self-protection measure: ``resolve_endpoint("utility")``
    silently falls back to the default chat endpoint when no utility endpoint is
    configured, which would steal the single warm llama.cpp slot.  Callers
    (``decide_use_web``) also guard this, but the check here ensures the
    function is safe to call directly.

    Note: if the user deliberately points ``utility_endpoint_id`` at the same
    endpoint as the default chat model that is their choice and is not detected.
    """
    from src.settings import load_settings
    if not (load_settings().get("utility_endpoint_id") or "").strip():
        return None

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
            text = _extract_reply_text(r.json()).strip().upper()
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
    (fact-seeking, named-entity lookups) the utility model is consulted.
    ``_ask_utility_model`` self-guards against running without an explicitly
    configured utility endpoint; the cheap pre-check here avoids an unnecessary
    async call when the setting is clearly absent.
    """
    verdict = heuristic_decision(message)
    if verdict == "yes":
        return True
    if verdict == "no":
        return False
    # ambiguous — quick pre-check avoids an async call when setting is absent;
    # _ask_utility_model also guards this internally.
    from src.settings import load_settings
    if (load_settings().get("utility_endpoint_id") or "").strip():
        answer = await _ask_utility_model(message)
        if answer is not None:
            return answer
    return False  # conservative default: no extra latency/noise


def apply_incognito(incognito: bool, use_web, decision):
    """Incognito chats must not send queries to search engines.

    Mirrors the RAG/memory suppression in build_chat_context. Pre-search is
    forced off; agent tools stay (the user explicitly invokes those).
    """
    if incognito and use_web:
        return False, "incognito-off"
    return use_web, decision


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
        _legacy_intent = (str(use_web).lower() == "true"
                          or str(allow_web_search).lower() == "true")
        if cfg not in ("off", "auto", "always") or _legacy_intent:
            # Manual / unrecognised / explicit legacy flags — leave untouched.
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
