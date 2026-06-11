"""Decide whether a chat message needs live web results (web_access=auto).

Two stages:
1. heuristic_decision(message) — instant regex pass: 'yes' | 'no' | 'ambiguous'
2. (next task) decide_use_web / resolve_web_access — async tie-break + mode mapping.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

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
