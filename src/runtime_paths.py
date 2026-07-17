"""Canonical, portable locations for Apollo runtime state."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


APP_NAME = "Apollo"


def repo_root() -> Path:
    """Return the source checkout root without depending on the current directory."""
    return Path(__file__).resolve().parents[1]


def legacy_data_root(repo: Path | None = None) -> Path:
    """Return the historical checkout-local data directory."""
    return (repo or repo_root()).resolve() / "data"


def platform_data_root(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the standard per-user data directory for a platform."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if platform.startswith("darwin"):
        return home / "Library" / "Application Support" / APP_NAME
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_NAME
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / APP_NAME.lower()


def _configured_path(value: str) -> Path:
    """Expand an explicit data-root setting without requiring it to exist."""
    return Path(value).expanduser().resolve()


def data_root(
    *,
    env: Mapping[str, str] | None = None,
    repo: Path | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve Apollo state storage with backward-compatible precedence.

    Existing checkouts continue using their populated legacy ``data/`` folder
    until the verified migration activates a platform directory. New installs
    without that folder use the platform default immediately.
    """
    env = os.environ if env is None else env
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = env.get(key)
        if value:
            return _configured_path(value)
    legacy = legacy_data_root(repo)
    if legacy.exists():
        return legacy
    return platform_data_root(platform=platform, env=env, home=home)


def data_path(
    *parts: str,
    env: Mapping[str, str] | None = None,
    repo: Path | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return an absolute path below the resolved Apollo data root."""
    return data_root(env=env, repo=repo, platform=platform, home=home).joinpath(*parts)
