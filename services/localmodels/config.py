"""Resolve and persist the directories scanned for local GGUF models."""
from __future__ import annotations

import os

from src.settings import load_settings, save_settings

ENV_VAR = "APOLLO_MODELS_DIRS"
BINARY_ENV_VAR = "APOLLO_LLAMA_SERVER"
MLX_PYTHON_ENV_VAR = "APOLLO_MLX_PYTHON"


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


# Architectures stock llama.cpp cannot load, mapped to the setting that names a
# fork build which can. k2-horizon GGUFs need MBZUAI-IFM's llama.cpp fork.
ARCH_BINARY_SETTINGS = {"k2-horizon": "llama_server_k2_path"}


def get_arch_llama_server_path(arch: str) -> str:
    """Fork llama-server configured for `arch`, or "" to use the default binary."""
    key = ARCH_BINARY_SETTINGS.get((arch or "").lower())
    if not key:
        return ""
    path = (load_settings().get(key) or "").strip()
    return os.path.expanduser(path) if path else ""


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


def get_mlx_python_path() -> str:
    """Python with mlx_lm installed: settings → env → "" (look for mlx_lm.server on PATH)."""
    path = (load_settings().get("mlx_python_path") or "").strip()
    if not path:
        path = os.getenv(MLX_PYTHON_ENV_VAR, "").strip()
    return os.path.expanduser(path) if path else ""


CONTEXT_ENV_VAR = "APOLLO_LLAMA_CONTEXT"
DEFAULT_LOCAL_CONTEXT = 16384
# 0 = auto: llama.cpp's --fit picks the largest window that fits in memory.
_CONTEXT_MIN, _CONTEXT_MAX = 2048, 1048576


def get_local_context() -> int:
    """Context window llama-server is launched with: settings → env → 16384.

    0 means auto (let llama.cpp size it to the machine's free memory).
    """
    value = load_settings().get("local_model_context")
    if value is None or value == "":
        value = os.getenv(CONTEXT_ENV_VAR, "").strip() or DEFAULT_LOCAL_CONTEXT
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LOCAL_CONTEXT
    return n if n == 0 or _CONTEXT_MIN <= n <= _CONTEXT_MAX else DEFAULT_LOCAL_CONTEXT


def set_local_context(n: int) -> int:
    """Persist the local context window; 0 = auto. Raises ValueError if out of range."""
    n = int(n)
    if n != 0 and not _CONTEXT_MIN <= n <= _CONTEXT_MAX:
        raise ValueError(f"context must be 0 (auto) or {_CONTEXT_MIN}-{_CONTEXT_MAX} tokens")
    settings = load_settings()
    settings["local_model_context"] = n
    save_settings(settings)
    return n


KV_CACHE_ENV_VAR = "APOLLO_LLAMA_KV_CACHE"
KV_CACHE_TYPES = ("q8_0", "f16")
DEFAULT_KV_CACHE = "q8_0"


def get_local_kv_cache() -> str:
    """KV-cache precision for llama.cpp chat models: settings → env → q8_0.

    q8_0 halves the memory a context window takes (measured: 43 GB → ~35 GB
    for a 27B Q8 at 262K) with no measurable quality loss; f16 is the
    full-precision original. A launch that fails with q8_0 (an architecture
    without flash attention) falls back to f16 on its own.
    """
    value = (load_settings().get("local_model_kv_cache") or "").strip().lower()
    if not value:
        value = os.getenv(KV_CACHE_ENV_VAR, "").strip().lower()
    return value if value in KV_CACHE_TYPES else DEFAULT_KV_CACHE


def set_local_kv_cache(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in KV_CACHE_TYPES:
        raise ValueError(f"kv cache must be one of {', '.join(KV_CACHE_TYPES)}")
    settings = load_settings()
    settings["local_model_kv_cache"] = value
    save_settings(settings)
    return value
