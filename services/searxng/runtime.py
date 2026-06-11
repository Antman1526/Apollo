"""Native lifecycle for the managed SearXNG sidecar: spawn and supervise
`python -m searx.webapp` from the dedicated venv in data/searxng/.

Mirrors services/paperclip/runtime.py: injectable spawn/health for tests,
graceful no-ops when disabled or not installed, never raises into startup.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import urllib.request
from typing import Callable, Optional

from services.searxng.config import SearxngConfig, load_config

logger = logging.getLogger(__name__)

_HEALTH_TTL = 2.0  # seconds — is_serving() is consulted on every search call


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


class SearxngRuntime:
    def __init__(self,
                 cfg_provider: Callable[[], SearxngConfig] = load_config,
                 spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
                 health_check: Callable[[str, float], bool] = _http_ok):
        self._cfg_provider = cfg_provider
        self._spawn = spawn
        self._health = health_check
        self._proc: Optional[subprocess.Popen] = None
        self._failed = False
        self._lock = threading.Lock()
        self._health_cache: tuple[float, bool] | None = None

    @property
    def url(self) -> str:
        return self._cfg_provider().url

    def _health_url(self) -> str:
        return self._cfg_provider().url.rstrip("/") + "/healthz"

    def is_serving(self) -> bool:
        """Health-checked, cached (2s TTL — called on every search)."""
        now = time.monotonic()
        if self._health_cache and (now - self._health_cache[0]) < _HEALTH_TTL:
            return self._health_cache[1]
        ok = self._health(self._health_url(), 2.0)
        self._health_cache = (now, ok)
        return ok

    def status(self) -> str:
        cfg = self._cfg_provider()
        if not cfg.enabled:
            return "disabled"
        if not cfg.installed:
            return "not_installed"
        if self.is_serving():
            return "running"
        if self._failed:
            return "failed"
        return "stopped"

    def start(self) -> bool:
        """Start the sidecar if enabled+installed. True when serving."""
        with self._lock:
            cfg = self._cfg_provider()
            if not cfg.enabled or not cfg.installed:
                return False
            self._health_cache = None
            if self.is_serving():
                logger.info("SearXNG already serving at %s — reusing", cfg.url)
                return True
            env = dict(os.environ)
            env["SEARXNG_SETTINGS_PATH"] = cfg.settings_path
            try:
                self._proc = self._spawn(
                    [cfg.venv_python, "-m", "searx.webapp"],
                    env=env,
                    cwd=cfg.home,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                logger.warning("SearXNG sidecar failed to spawn: %s", e)
                self._failed = True
                return False
            # Wait briefly for boot; callers can also poll status().
            for _ in range(30):
                self._health_cache = None
                if self.is_serving():
                    logger.info("SearXNG sidecar serving at %s", cfg.url)
                    self._failed = False
                    return True
                if self._proc.poll() is not None:
                    logger.warning("SearXNG sidecar exited during boot")
                    self._failed = True
                    return False
                time.sleep(1)
            self._failed = True
            return False

    def stop(self):
        with self._lock:
            self._health_cache = None
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=10)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None


_runtime: SearxngRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> SearxngRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = SearxngRuntime()
        return _runtime
