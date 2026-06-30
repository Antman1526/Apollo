"""Build a minimal, secret-free environment for agent-spawned subprocesses.

SECURITY-FIXLIST P1 #2. The agent's ``bash``/``python`` tools, background jobs,
the shell service, and MCP stdio servers all used to inherit the FULL host
``os.environ`` — every provider API key, ``DATABASE_URL``, decrypted SMTP/IMAP
password, ``SEARXNG_SECRET``, etc. A prompt-injected agent or malicious skill
could ``env | curl`` them straight out.

This builds an **allowlisted** env instead (default-deny), mirroring the proven
sidecar pattern in ``services/searxng/runtime.py``. A denylist scrub is layered
on top of the opt-in passthrough as defense in depth, so even an explicitly
allowed var can never carry a secret-shaped value.

Dependency-light (stdlib + ``settings_scrub``) so it can be imported anywhere in
the subprocess-spawning paths without dragging in the app/db/auth chain.
"""
import os
from typing import Dict, Iterable, Optional

from src.settings_scrub import is_secret_key

# Safe, non-secret vars a subprocess legitimately needs to function. Mirrors the
# SearXNG sidecar allowlist, plus the common toolchain/locale/Windows vars so the
# agent's bash/python tools behave normally.
_PASS = (
    # POSIX core
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "TMPDIR", "TEMP", "TMP",
    # Windows core
    "SYSTEMROOT", "WINDIR", "USERPROFILE", "COMSPEC", "PATHEXT",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)

# Explicit secret-shaped names not caught by ``settings_scrub``'s suffix rules.
_DENY_EXACT = frozenset({"DATABASE_URL"})
# The fixlist calls out SMTP_*/IMAP_* explicitly; strip the whole namespace.
_DENY_PREFIXES = ("SMTP_", "IMAP_")


def is_secret_env(name: str) -> bool:
    """True if ``name`` looks like a secret-bearing env var and must be stripped."""
    n = (name or "").upper()
    if n in _DENY_EXACT:
        return True
    if any(n.startswith(p) for p in _DENY_PREFIXES):
        return True
    # Suffix-based detection (_API_KEY, _TOKEN, _SECRET, _PASSWORD, _KEY, ...).
    return is_secret_key(name)


def build_agent_env(
    extra: Optional[Dict[str, str]] = None,
    passthrough: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """Return a minimal env for an agent-spawned subprocess.

    - Starts from the ``_PASS`` allowlist (default-deny: host secrets are simply
      never copied because they aren't on the list).
    - ``passthrough`` is an optional admin-configured list of *extra* host var
      names to opt in (e.g. ``GH_HOST``), each still denylist-filtered so a
      secret can never be opted in by mistake.
    - ``extra`` is caller-supplied literals (e.g. ``TERM``/``COLUMNS`` or NPM
      quieting flags) merged last; these are trusted constants, not host values.
    """
    env: Dict[str, str] = {k: os.environ[k] for k in _PASS if k in os.environ}

    for name in (passthrough or ()):
        if name in os.environ and not is_secret_env(name):
            env[name] = os.environ[name]

    if extra:
        env.update(extra)

    return env
