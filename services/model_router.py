"""Mixture routing: send light chat messages to a small, fast local model.

Apollo is model-neutral, so it can do what single-vendor tools won't: pick
the model per message purely on task fit. A deterministic classifier (no
LLM call, no added latency) tags a message "light" or "heavy"; light ones
are answered by the configured `light` role (Settings: light_endpoint_id /
light_model) with the session's own model as first fallback, so a light-lane
failure degrades to exactly the old behavior.

Chat mode only — the agent loop needs tool-competent models and is not
routed. Opt-in via the `mixture_routing_enabled` setting (default off).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

from src.settings import get_setting

logger = logging.getLogger(__name__)

# A message is LIGHT only when it is short and clearly conversational.
# Everything ambiguous is heavy — a wrong "heavy" costs a few seconds, a
# wrong "light" costs answer quality.
LIGHT_MAX_CHARS = 280

_HEAVY_MARKERS = re.compile(
    r"(?i)\b("
    r"code|write|implement|debug|fix|refactor|analy[sz]e|research|review|"
    r"plan|design|architect|prove|derive|calculate|compute|translate|"
    r"summari[sz]e|compare|explain why|step[- ]by[- ]step|essay|report|"
    r"document|spreadsheet|regex|sql|script|function|error|traceback|"
    r"stack trace"
    r")\b"
)


def classify_message(message: str) -> str:
    """Return "light" or "heavy". Deterministic; conservative toward heavy."""
    msg = (message or "").strip()
    if not msg or len(msg) > LIGHT_MAX_CHARS:
        return "heavy"
    if "```" in msg or "\n\n" in msg:
        return "heavy"
    if _HEAVY_MARKERS.search(msg):
        return "heavy"
    if msg.count("?") > 1:
        return "heavy"
    return "light"


def route_chat(
    message: str, owner: Optional[str] = None
) -> Optional[Tuple[str, str, Dict]]:
    """(url, model, headers) for the light lane, or None to keep the default.

    None whenever routing is disabled, the message is heavy, no light model
    is configured, or anything at all goes wrong — the caller treats None
    as "behave exactly as before".
    """
    try:
        if not get_setting("mixture_routing_enabled", False):
            return None
        if classify_message(message) != "light":
            return None
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("light", owner=owner)
        if not url or not model:
            return None
        return url, model, headers or {}
    except Exception:
        logger.exception("mixture routing failed (falling back to default)")
        return None
