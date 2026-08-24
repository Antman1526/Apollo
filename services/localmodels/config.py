"""Resolve and persist the directories scanned for local GGUF models."""
from __future__ import annotations

import os

from src.settings import load_settings, save_settings

ENV_VAR = "APOLLO_MODELS_DIRS"
BINARY_ENV_VAR = "APOLLO_LLAMA_SERVER"


def _default_dirs() -> list[str]:
    """Built-in scan roots per platform (used only when nothing is configured)."""
    if os.name == "nt":
        home = os.path.expanduser("~")
        return [
            os.path.join(home, "Desktop", "AI_Models"),
            os.path.join(home, "AI_Models"),
            os.path.join(home, ".lmstudio", "models"),
        ]
    return [
        "/Volumes/MainStore/Development/AI_Models",
        os.path.expanduser("~/Desktop/AI_Models"),
    ]


DEFAULT_DIRS = _default_dirs()


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


def get_llama_server_path() -> str:
    """Configured llama-server binary: settings → env → "" (auto-detect)."""
    settings = load_settings()
    path = (settings.get("llama_server_path") or "").strip()
    if path:
        return os.path.expanduser(path)
    env = os.getenv(BINARY_ENV_VAR, "").strip()
    if env:
        return os.path.expanduser(env)
    return ""


def set_llama_server_path(path: str) -> str:
    """Persist the llama-server binary path; "" clears it (back to auto-detect).

    Same guard as the scan dirs: a relative path is dropped so a caller can't
    persist a binary that resolves differently per working directory.
    """
    p = os.path.expanduser((path or "").strip())
    if p and not os.path.isabs(p):
        p = ""
    settings = load_settings()
    settings["llama_server_path"] = p
    save_settings(settings)
    return p
