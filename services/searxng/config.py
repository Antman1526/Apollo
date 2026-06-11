"""Configuration for the managed SearXNG sidecar.

Apollo installs SearXNG natively (no Docker) into data/searxng/:
    data/searxng/src/         git checkout of searxng/searxng
    data/searxng/venv/        dedicated virtualenv
    data/searxng/settings.yml minimal localhost-only config (JSON API on)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from src.constants import DATA_DIR

SEARXNG_HOME = os.path.join(DATA_DIR, "searxng")
DEFAULT_PORT = 8893


@dataclass(frozen=True)
class SearxngConfig:
    enabled: bool
    port: int
    home: str = SEARXNG_HOME

    @property
    def venv_python(self) -> str:
        sub, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
        return os.path.join(self.home, "venv", sub, exe)

    @property
    def settings_path(self) -> str:
        return os.path.join(self.home, "settings.yml")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def installed(self) -> bool:
        return os.path.exists(self.venv_python) and os.path.exists(self.settings_path)


def load_config() -> SearxngConfig:
    from src.settings import load_settings

    s = load_settings()
    try:
        port = int(s.get("searxng_port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return SearxngConfig(enabled=bool(s.get("searxng_managed", True)), port=port)
