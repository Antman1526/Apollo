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
    """Persist the directory list and return the cleaned value.

    Entries are expanded (`~`) and must be absolute paths; relative or empty
    entries are dropped so a caller can't seed a surprise relative scan root.
    """
    cleaned = []
    for d in dirs or []:
        if not d or not d.strip():
            continue
        p = os.path.expanduser(d.strip())
        if os.path.isabs(p):
            cleaned.append(p)
    settings = load_settings()
    settings["local_model_dirs"] = cleaned
    save_settings(settings)  # save_settings() invalidates the settings cache
    return cleaned
