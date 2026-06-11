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

from src.constants import BASE_DIR
from services.searxng.config import SearxngConfig, load_config

logger = logging.getLogger(__name__)

_HEALTH_TTL = 2.0  # seconds — is_serving() is consulted on every search call
_RESTART_COOLDOWN = 300.0  # seconds between automatic restart attempts
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — truncate before each spawn

# Patchable in tests via monkeypatch on this module attribute.
_LOG_PATH: str = os.path.join(BASE_DIR, "logs", "searxng.log")


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
        self._stopping = threading.Event()
        self._last_restart_attempt: Optional[float] = None
        self._log_fh = None  # open file handle for stdout/stderr; closed on stop()

    @property
    def url(self) -> str:
        return self._cfg_provider().url

    @property
    def installed(self) -> bool:
        return self._cfg_provider().installed

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
        """Start the sidecar if enabled+installed. True when serving.

        The lock is held only for the config/reuse check and the spawn itself.
        The up-to-30s health-wait loop runs *outside* the lock so that stop()
        can interrupt it promptly via the _stopping event.
        """
        with self._lock:
            cfg = self._cfg_provider()
            if not cfg.enabled or not cfg.installed:
                return False
            # A previous stop() leaves the event set; clear it so a restart
            # (e.g. after install) doesn't abort its own boot wait.
            self._stopping.clear()
            self._health_cache = None
            if self._proc is not None and self._proc.poll() is None:
                # Another caller spawned between its lock release and ours;
                # don't double-spawn onto the same port.
                return True
            if self.is_serving():
                logger.info("SearXNG already serving at %s — reusing", cfg.url)
                return True
            # Minimal env: the sidecar needs none of Apollo's secrets.
            _PASS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
                     "SYSTEMROOT", "WINDIR", "USERPROFILE")
            env = {k: os.environ[k] for k in _PASS if k in os.environ}
            env["SEARXNG_SETTINGS_PATH"] = cfg.settings_path
            # Open the sidecar log, truncating if it exceeds 5 MB so it
            # never grows without bound.  _LOG_PATH is module-level for
            # easy monkeypatching in tests.
            try:
                # A watchdog restart can reach here with a stale handle from
                # the crashed run — close it before opening a fresh one.
                if self._log_fh is not None:
                    try:
                        self._log_fh.close()
                    except Exception:
                        pass
                    self._log_fh = None
                log_path = _LOG_PATH
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                if os.path.exists(log_path) and os.path.getsize(log_path) > _LOG_MAX_BYTES:
                    open(log_path, "w").close()  # truncate
                _log_fh = open(log_path, "a")  # noqa: WPS515
                self._log_fh = _log_fh
            except Exception as _log_exc:
                logger.warning("SearXNG log open failed (%s); falling back to DEVNULL", _log_exc)
                _log_fh = subprocess.DEVNULL
                self._log_fh = None
            try:
                self._proc = self._spawn(
                    [cfg.venv_python, "-m", "searx.webapp"],
                    env=env,
                    cwd=cfg.home,
                    stdout=_log_fh,
                    stderr=_log_fh,
                )
            except Exception as e:
                logger.warning("SearXNG sidecar failed to spawn: %s", e)
                self._failed = True
                _fh = self._log_fh
                self._log_fh = None
                try:
                    if _fh is not None:
                        _fh.close()
                except Exception:
                    pass
                return False
            proc = self._proc

        # Boot-wait loop — lock is NOT held here so stop() can run concurrently.
        # _stopping.wait(1) blocks for up to 1 s but wakes immediately when
        # stop() calls _stopping.set(), keeping shutdown latency near zero.
        for _ in range(30):
            if self._stopping.is_set():
                logger.info("SearXNG boot-wait interrupted by stop()")
                self._failed = False
                return False
            self._health_cache = None
            if self.is_serving():
                logger.info("SearXNG sidecar serving at %s", cfg.url)
                self._failed = False
                return True
            if proc.poll() is not None:
                logger.warning("SearXNG sidecar exited during boot")
                self._failed = True
                _fh = self._log_fh
                self._log_fh = None
                try:
                    if _fh is not None:
                        _fh.close()
                except Exception:
                    pass
                return False
            self._stopping.wait(1)
        self._failed = True
        return False

    def stop(self):
        # Signal the boot-wait loop to exit without waiting for the next
        # sleep tick (mirrors paperclip runtime's pattern of releasing the
        # lock before any blocking syscall).
        self._stopping.set()
        with self._lock:
            proc = self._proc
            self._proc = None
            self._health_cache = None
            log_fh = self._log_fh
            self._log_fh = None
        # Terminate/wait/kill *outside* the lock so we never hold it across
        # a blocking wait (terminate+wait can block up to 10 s).
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        # Close the log file handle after the process is done writing.
        try:
            if log_fh is not None:
                log_fh.close()
        except Exception:
            pass


    def maybe_restart(self) -> bool:
        """Schedule a background restart of a crashed sidecar, at most once
        per cooldown window. Returns True if a restart was scheduled.

        Called from the search hot path (via _searxng_definitely_down) when
        the managed sidecar is installed but not serving — so it must never
        block and never raise.
        """
        try:
            cfg = self._cfg_provider()
            if not cfg.enabled or not cfg.installed:
                return False
            now = time.monotonic()
            if self._last_restart_attempt is not None and \
                    (now - self._last_restart_attempt) < _RESTART_COOLDOWN:
                return False
            self._last_restart_attempt = now
            threading.Thread(target=self.start, name="searxng-restart",
                             daemon=True).start()
            return True
        except Exception:
            return False


_runtime: SearxngRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> SearxngRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = SearxngRuntime()
        return _runtime
