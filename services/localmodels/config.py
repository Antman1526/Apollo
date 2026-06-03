"""Resolve and persist the directories scanned for local GGUF models."""
from __future__ import annotations

import os

from src.settings import load_settings, save_settings

ENV_VAR = "APOLLO_MODELS_DIRS"
DEFAULT_DIRS = [
    "/Volumes/MainStore/Development/AI_Models",
    os.path.expanduser("~/Desktop/AI_Models"),
]


def _parse_env(raw: str) -> list[str]:
    sep = os.pathsep if os.pathsep in raw else ","
    return [p.strip() for p in raw.split(sep) if p.strip()]


def get_local_model_dirs() -> list[str]:
    """Configured dirs (settings) → env seed → built-in defaults."""
    settings = load_settings()
    dirs = settings.get("local_model_dirs") or []
    dirs = [d for d in dirs if d and d.strip()]
    if dirs:
        return dirs
    env = os.getenv(ENV_VAR, "")
    if env.strip():
        return _parse_env(env)
    return list(DEFAULT_DIRS)


def set_local_model_dirs(dirs: list[str]) -> list[str]:
    """Persist the directory list and return the cleaned value."""
    cleaned = [d.strip() for d in (dirs or []) if d and d.strip()]
    settings = load_settings()
    settings["local_model_dirs"] = cleaned
    save_settings(settings)
    # Invalidate the settings cache so the next read sees the new value.
    import src.settings as _s
    _s._settings_cache = None
    return cleaned
