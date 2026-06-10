"""Resolve Paperclip integration settings from environment + a secret file.

Pure-ish: only touches env and an on-disk secret file. No network, no DB.
Mirrors the env-driven style of services/localmodels/config.py.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}

# Default model endpoints. In Docker the Apollo/Paperclip containers reach an
# Ollama running on the host via host.docker.internal (Mac/Windows; Linux gets
# an extra_hosts mapping in docker-compose.yml). Native/external mode runs on
# the host itself, where that name does not resolve — use localhost instead.
_OLLAMA_DOCKER = "http://host.docker.internal:11434/v1"
_OLLAMA_LOCAL = "http://localhost:11434/v1"


@dataclass(frozen=True)
class PaperclipConfig:
    enabled: bool
    mode: str            # docker | native | external | off
    url: str             # server-side base Apollo can reach, no trailing slash
    browser_url: str     # origin the browser iframes directly, no trailing slash
    port: int
    model_endpoint: str  # ollama | apollo | custom
    model_base_url: str
    model_name: str


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _resolve_model(endpoint: str, mode: str = "docker") -> tuple[str, str]:
    """Return (base_url, model_name) for the selected endpoint."""
    if endpoint == "custom":
        return (
            os.getenv("PAPERCLIP_MODEL_BASE_URL", ""),
            os.getenv("PAPERCLIP_MODEL_NAME", ""),
        )
    if endpoint == "apollo":
        # Phase 3 adds the Apollo /v1 proxy; in Docker that's the in-cluster
        # apollo host, natively it's the local Apollo instance.
        default_apollo = "http://apollo:7000/v1" if mode == "docker" else "http://localhost:7000/v1"
        return (
            os.getenv("PAPERCLIP_MODEL_BASE_URL", default_apollo),
            os.getenv("PAPERCLIP_MODEL_NAME", ""),
        )
    # ollama (default)
    default_ollama = _OLLAMA_DOCKER if mode == "docker" else _OLLAMA_LOCAL
    return (
        os.getenv("PAPERCLIP_MODEL_BASE_URL", default_ollama),
        os.getenv("PAPERCLIP_MODEL_NAME", ""),
    )


def load_config() -> PaperclipConfig:
    enabled = _bool("PAPERCLIP_ENABLED", False)
    mode = os.getenv("PAPERCLIP_MODE", "docker").strip().lower()
    port = int(os.getenv("PAPERCLIP_PORT", "3100"))
    # Server-side URL Apollo can reach: a Compose service name under Docker, but
    # localhost when Paperclip runs natively or as an already-running instance.
    default_url = f"http://paperclip:{port}" if mode == "docker" else f"http://localhost:{port}"
    url = os.getenv("PAPERCLIP_URL", default_url).rstrip("/")
    # The browser iframes Paperclip's own origin directly (its UI + /api are
    # hard-wired to root paths, so it cannot be embedded under an Apollo subpath).
    browser_url = os.getenv("PAPERCLIP_BROWSER_URL", f"http://localhost:{port}").rstrip("/")
    endpoint = os.getenv("PAPERCLIP_MODEL_ENDPOINT", "ollama").strip().lower()
    base_url, model_name = _resolve_model(endpoint, mode)
    return PaperclipConfig(
        enabled=enabled, mode=mode, url=url, browser_url=browser_url, port=port,
        model_endpoint=endpoint, model_base_url=base_url, model_name=model_name,
    )


def _read_or_make_secret(env_name: str, file_env: str, default_file: str) -> str:
    env = os.getenv(env_name)
    if env:
        return env
    path = os.getenv(file_env, os.path.expanduser(default_file))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass
    secret = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret


def resolve_auth_secret() -> str:
    """Return a stable BETTER_AUTH_SECRET, generating + persisting one if unset."""
    return _read_or_make_secret(
        "PAPERCLIP_AUTH_SECRET", "PAPERCLIP_SECRET_FILE", "~/.apollo/paperclip_secret")


def resolve_proxy_token() -> str:
    """Bearer token guarding Apollo's local-model proxy. Passed to Paperclip's
    opencode agents as OPENAI_API_KEY; validated by routes/lmproxy_routes."""
    return _read_or_make_secret(
        "PAPERCLIP_PROXY_TOKEN", "PAPERCLIP_PROXY_TOKEN_FILE", "~/.apollo/paperclip_proxy_token")
