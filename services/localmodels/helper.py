"""The helper model: a small local model that works beside the main one.

It takes the utility role (compaction summaries, memory and skill
extraction, auto-naming, the web decider) and the Fast Lane (short chat
messages) whenever those roles are unset, and runs in its own slot so it
never evicts the model the user is talking to. Chosen in Settings or picked
automatically: the smallest capable chat model in the library.
"""
from __future__ import annotations

from typing import Optional

from services.localmodels.scanner import LocalModel
from src.settings import load_settings

# Auto-pick window: big enough to be useful for summaries and quick
# answers, small enough to stay resident next to a 27B.
_MIN_GB, _MAX_GB = 3.5, 12.0
_MIN_CONTEXT = 32768
# Newer families first; within a family the smallest wins.
_ARCH_RANK = {"qwen35": 0, "qwen3vl": 0, "qwen35moe": 0, "gemma4": 0, "k2-horizon": 1,
              "qwen3": 1, "qwen3moe": 1, "gemma3": 2, "llama": 3, "phi3": 4}


def pick_helper(models: list[LocalModel]) -> Optional[LocalModel]:
    """Deterministic auto-pick; None when nothing in the library qualifies."""
    def ok(m):
        gb = (m.size_bytes or 0) / 2**30
        return (m.kind == "chat" and m.backend == "llama.cpp" and _MIN_GB <= gb <= _MAX_GB
                and (m.native_context or 0) >= _MIN_CONTEXT and m.tools is not False)
    cands = [m for m in models if ok(m)]
    if not cands:
        return None
    return min(cands, key=lambda m: (_ARCH_RANK.get((m.arch or "").lower(), 9), m.size_bytes, m.name))


def get_helper(models: list[LocalModel]) -> Optional[LocalModel]:
    """The configured helper if it is in the catalog, else the auto-pick."""
    settings = load_settings()
    name = (settings.get("helper_model") or "").strip()
    if name:
        for m in models:
            if m.name == name and m.kind == "chat":
                return m
    if settings.get("helper_model_auto", True):
        return pick_helper(models)
    return None


def helper_name() -> Optional[str]:
    from services.localmodels.server_manager import get_server
    m = get_helper(get_server().catalog())
    return m.name if m else None
