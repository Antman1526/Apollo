"""Native lifecycle for the bundled Paperclip: locate Node, spawn and supervise
`paperclipai run`, and point its opencode agents at Apollo's local-model proxy.

Paperclip is self-contained (it provisions its own embedded Postgres via
`@embedded-postgres`), so Apollo only manages the single Node process. Only
active when `mode == native` and enabled; external/docker modes are no-ops
(the user or Compose owns the process). Missing Node degrades gracefully —
it never raises into Apollo startup.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Callable, Dict, List, Optional

from services.paperclip.config import PaperclipConfig

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "2026.529.0"

# Common locations where Node may live when not on a GUI app's PATH (mirrors the
# llama-server discovery in services/localmodels/server_manager.py).
_NODE_CANDIDATES = [
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    os.path.expanduser("~/.local/bin/node"),
]
_NPX_CANDIDATES = [
    "/opt/homebrew/bin/npx",
    "/usr/local/bin/npx",
    os.path.expanduser("~/.local/bin/npx"),
]


def _find(env_var: str, which_name: str, candidates: List[str],
          which: Callable[[str], Optional[str]]) -> Optional[str]:
    bundled = os.getenv(env_var)
    if bundled and os.path.exists(bundled):
        return bundled
    found = which(which_name)
    if found:
        return found
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def find_node(which: Callable[[str], Optional[str]] = shutil.which,
              candidates: Optional[List[str]] = None) -> Optional[str]:
    return _find("PAPERCLIP_NODE_BIN", "node",
                 candidates if candidates is not None else _NODE_CANDIDATES, which)


def find_npx(which: Callable[[str], Optional[str]] = shutil.which,
             candidates: Optional[List[str]] = None) -> Optional[str]:
    return _find("PAPERCLIP_NPX_BIN", "npx",
                 candidates if candidates is not None else _NPX_CANDIDATES, which)


def build_env(cfg: PaperclipConfig, proxy_token: str, proxy_base: str,
              base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for `paperclipai run`: bind locally and route opencode agents
    at Apollo's local-model proxy."""
    env = dict(base_env if base_env is not None else os.environ)
    env["PORT"] = str(cfg.port)
    env["HOST"] = "127.0.0.1"
    if os.getenv("PAPERCLIP_HOME"):
        env["PAPERCLIP_HOME"] = os.environ["PAPERCLIP_HOME"]
    # opencode-local → Apollo local-model proxy (OpenAI-compatible).
    env["OPENAI_BASE_URL"] = proxy_base
    env["OPENAI_API_KEY"] = proxy_token
    env["OPENCODE_ALLOW_ALL_MODELS"] = "true"
    return env


def build_command(node: str, npx: Optional[str], cli: Optional[str],
                  version: str = DEFAULT_VERSION) -> List[str]:
    if cli:
        return [node, cli, "run"]
    return [npx, "-y", f"paperclipai@{version}", "run"]


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


class PaperclipRuntime:
    def __init__(self, cfg: PaperclipConfig,
                 proxy_token_provider: Callable[[], str],
                 proxy_base_provider: Callable[[], str],
                 spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
                 health_check: Callable[[str, float], bool] = _http_ok,
                 node_finder: Callable[[], Optional[str]] = find_node,
                 npx_finder: Callable[[], Optional[str]] = find_npx):
        self._cfg = cfg
        self._token = proxy_token_provider
        self._base = proxy_base_provider
        self._spawn = spawn
        self._health = health_check
        self._find_node = node_finder
        self._find_npx = npx_finder
        self._proc: Optional[subprocess.Popen] = None
        self._reused = False
        self._lock = threading.Lock()

    def _already_serving(self) -> bool:
        return self._health(self._cfg.url.rstrip("/") + "/api/health", 2.0)

    def start(self) -> bool:
        """Spawn paperclipai if we own its lifecycle. Returns True if running.
        Never raises — missing Node logs a warning and disables the feature."""
        if not self._cfg.enabled or self._cfg.mode != "native":
            return False
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            # Reuse an already-running Paperclip on this port (the user's own
            # `paperclipai run`, or a previous Apollo launch) rather than
            # spawning a duplicate or colliding on the port.
            if self._already_serving():
                self._reused = True
                logger.info("Paperclip already running at %s — reusing it.", self._cfg.url)
                return True
            node = self._find_node()
            if not node:
                logger.warning(
                    "Paperclip enabled (native) but Node was not found. Install "
                    "Node or set PAPERCLIP_NODE_BIN; Paperclip will stay off.")
                return False
            cli = os.getenv("PAPERCLIP_CLI")
            npx = None if cli else self._find_npx()
            if not cli and not npx:
                logger.warning("Paperclip: neither PAPERCLIP_CLI nor npx found; staying off.")
                return False
            version = os.getenv("PAPERCLIP_VERSION", DEFAULT_VERSION)
            cmd = build_command(node, npx, cli, version)
            env = build_env(self._cfg, self._token(), self._base())
            logger.info("Starting Paperclip: %s (PORT=%s)", " ".join(cmd), self._cfg.port)
            try:
                self._proc = self._spawn(cmd, env=env, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.STDOUT)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Paperclip failed to start: %s", e)
                self._proc = None
                return False
            return True

    def wait_healthy(self, timeout: float = 60.0) -> bool:
        url = self._cfg.url.rstrip("/") + "/api/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if self._health(url, 2.0):
                return True
            time.sleep(1.0)
        return False

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def status(self) -> dict:
        running = (self._proc is not None and self._proc.poll() is None) or self._reused
        return {"mode": self._cfg.mode, "managed": self._cfg.mode == "native",
                "running": running, "reused": self._reused, "url": self._cfg.url}
