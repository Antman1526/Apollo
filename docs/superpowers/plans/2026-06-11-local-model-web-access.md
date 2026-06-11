# Local-Model Web Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local GGUF models search and read the web automatically during chat via a no-Docker managed SearXNG sidecar with immediate DuckDuckGo fallback, plus an auto-search decider, with Deep Research / agent web tools / crawl4ai verified end to end.

**Architecture:** A new `services/searxng/` sidecar manager (modeled on `services/paperclip/runtime.py`) supervises a venv-installed SearXNG bound to `127.0.0.1`. The search provider chain in `services/search/core.py` skips SearXNG instantly when the sidecar is down. A new `src/web_decider.py` resolves a tri-state `web_access` mode (off/auto/always) sent by the chat UI; in `auto` it decides per message (heuristic + optional utility-model tie-break) and reuses the existing `use_web` pre-search injection in `src/chat_processor.py`.

**Tech Stack:** Python 3.11+/FastAPI, httpx, pytest, vanilla-JS frontend (no build step), SearXNG (git checkout + venv), shell script for setup.

**Spec:** `docs/superpowers/specs/2026-06-11-local-model-web-access-design.md`

**Conventions used below:**
- Run tests with: `venv/bin/python -m pytest <file> -v` from the repo root `/Users/Antman/Apollo`.
- Commit after every green test. Conventional Commits style (repo uses `feat:`, `fix:`, `docs:`).

---

### Task 1: Settings keys for sidecar + web access mode

**Files:**
- Modify: `src/settings.py` (DEFAULT_SETTINGS, around line 51 where `search_provider` lives)
- Test: `tests/test_web_access_settings.py`

- [ ] **Step 1: Write the failing test**

```python
"""Defaults for the managed SearXNG sidecar and web-access mode."""
from src.settings import DEFAULT_SETTINGS


def test_searxng_sidecar_defaults():
    assert DEFAULT_SETTINGS["searxng_managed"] is True
    assert DEFAULT_SETTINGS["searxng_port"] == 8893


def test_web_access_mode_default_is_manual():
    # "manual" preserves legacy toggle behavior until the user opts in.
    assert DEFAULT_SETTINGS["web_access_mode"] == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_web_access_settings.py -v`
Expected: FAIL with `KeyError: 'searxng_managed'`

- [ ] **Step 3: Add the keys**

In `src/settings.py`, directly below the `"search_fallback_chain": ["duckduckgo"],` entry, add:

```python
    # Managed SearXNG sidecar (no Docker): Apollo installs SearXNG into
    # data/searxng/ and supervises it like the Paperclip sidecar. When the
    # sidecar isn't running, the provider chain skips straight to the
    # fallback (DuckDuckGo) with no timeout penalty.
    "searxng_managed": True,
    "searxng_port": 8893,
    # Default web access behavior when the client doesn't send web_access:
    # "manual" — legacy per-message toggles only
    # "auto"   — decider chooses per message whether to pre-search
    # "always" — pre-search every chat message
    "web_access_mode": "manual",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_web_access_settings.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/settings.py tests/test_web_access_settings.py
git commit -m "feat(search): settings keys for managed SearXNG sidecar and web_access_mode"
```

---

### Task 2: SearXNG sidecar config module

**Files:**
- Create: `services/searxng/__init__.py` (empty)
- Create: `services/searxng/config.py`
- Test: `tests/test_searxng_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""SearxngConfig: paths, url, installed detection."""
import os

from services.searxng.config import SearxngConfig, load_config


def test_paths_derive_from_home(tmp_path):
    cfg = SearxngConfig(enabled=True, port=9001, home=str(tmp_path))
    assert cfg.url == "http://127.0.0.1:9001"
    assert cfg.settings_path == os.path.join(str(tmp_path), "settings.yml")
    assert cfg.venv_python.startswith(os.path.join(str(tmp_path), "venv"))


def test_installed_requires_python_and_settings(tmp_path):
    cfg = SearxngConfig(enabled=True, port=9001, home=str(tmp_path))
    assert cfg.installed is False
    os.makedirs(os.path.dirname(cfg.venv_python), exist_ok=True)
    open(cfg.venv_python, "w").close()
    assert cfg.installed is False  # settings.yml still missing
    open(cfg.settings_path, "w").close()
    assert cfg.installed is True


def test_load_config_reads_settings(monkeypatch):
    monkeypatch.setattr(
        "src.settings.load_settings",
        lambda: {"searxng_managed": False, "searxng_port": "9100"},
    )
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.port == 9100


def test_load_config_bad_port_falls_back(monkeypatch):
    monkeypatch.setattr(
        "src.settings.load_settings",
        lambda: {"searxng_managed": True, "searxng_port": "not-a-number"},
    )
    assert load_config().port == 8893
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_searxng_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.searxng'`

- [ ] **Step 3: Implement**

`services/searxng/__init__.py` — empty file.

`services/searxng/config.py`:

```python
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


@dataclass
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
```

Note: `load_config` imports `load_settings` inside the function so `monkeypatch.setattr("src.settings.load_settings", ...)` works and module import order stays cheap.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_searxng_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/searxng/__init__.py services/searxng/config.py tests/test_searxng_config.py
git commit -m "feat(searxng): sidecar config module (paths, port, installed detection)"
```

---

### Task 3: SearXNG runtime manager

**Files:**
- Create: `services/searxng/runtime.py`
- Test: `tests/test_searxng_runtime.py`

The runtime mirrors `services/paperclip/runtime.py`: injectable `spawn` and `health_check` for tests, never raises into startup, supervises one subprocess.

- [ ] **Step 1: Write the failing test**

```python
"""SearxngRuntime lifecycle with fake spawn/health."""
import os

from services.searxng.config import SearxngConfig
from services.searxng.runtime import SearxngRuntime


def _cfg(tmp_path, enabled=True, installed=True, port=9001):
    cfg = SearxngConfig(enabled=enabled, port=port, home=str(tmp_path))
    if installed:
        os.makedirs(os.path.dirname(cfg.venv_python), exist_ok=True)
        open(cfg.venv_python, "w").close()
        open(cfg.settings_path, "w").close()
    return cfg


class FakeProc:
    def __init__(self, *a, **kw):
        self.args = a
        self.kwargs = kw
        self.killed = False

    def poll(self):
        return 1 if self.killed else None

    def terminate(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_disabled_is_noop(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path, enabled=False),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    assert rt.start() is False
    assert rt.status() == "disabled"


def test_not_installed(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path, installed=False),
                        spawn=FakeProc, health_check=lambda u, t=2.0: False)
    assert rt.start() is False
    assert rt.status() == "not_installed"


def test_start_spawns_webapp_with_settings_env(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    health = {"ok": False}
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=spawn, health_check=lambda u, t=2.0: health["ok"])
    health["ok"] = True  # becomes healthy right after spawn
    assert rt.start() is True
    cmd = spawned[0].args[0]
    assert cmd[1:] == ["-m", "searx.webapp"]
    env = spawned[0].kwargs["env"]
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert rt.status() == "running"


def test_reuses_already_serving_instance(tmp_path):
    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path),
                        spawn=FakeProc, health_check=lambda u, t=2.0: True)
    assert rt.start() is True
    assert rt.status() == "running"
    assert rt._proc is None  # nothing spawned — external/prior instance reused


def test_stop_terminates(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    health = {"ok": False}

    def check(u, t=2.0):
        return health["ok"]

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    health["ok"] = True
    rt.start()
    rt.stop()
    if spawned:
        assert spawned[0].killed is True


def test_is_serving_caches_health(tmp_path):
    calls = []

    def check(u, t=2.0):
        calls.append(u)
        return True

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=FakeProc, health_check=check)
    assert rt.is_serving() is True
    assert rt.is_serving() is True
    assert len(calls) == 1  # second call within TTL served from cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_searxng_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` for `services.searxng.runtime`

- [ ] **Step 3: Implement**

`services/searxng/runtime.py`:

```python
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
```

Note for the `test_start_spawns_webapp_with_settings_env` test: `start()` clears the health cache before the pre-spawn `is_serving()` check, so set `health["ok"] = True` *after* constructing but the fake starts unhealthy — the test above flips it before `start()`, which makes the pre-spawn check succeed and nothing spawns. **Fix the test ordering as written below instead:** the fake health must return False for the first call (pre-spawn reuse check) and True afterwards. Replace the `health` flag in that test with a counter:

```python
def test_start_spawns_webapp_with_settings_env(tmp_path):
    spawned = []

    def spawn(*a, **kw):
        p = FakeProc(*a, **kw)
        spawned.append(p)
        return p

    calls = {"n": 0}

    def check(u, t=2.0):
        calls["n"] += 1
        return calls["n"] > 1  # unhealthy pre-spawn, healthy after

    rt = SearxngRuntime(cfg_provider=lambda: _cfg(tmp_path), spawn=spawn, health_check=check)
    assert rt.start() is True
    cmd = spawned[0].args[0]
    assert cmd[1:] == ["-m", "searx.webapp"]
    env = spawned[0].kwargs["env"]
    assert env["SEARXNG_SETTINGS_PATH"].endswith("settings.yml")
    assert rt.status() == "running"
```

(Use this counter version in Step 1 — it is the correct test.)

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_searxng_runtime.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/searxng/runtime.py tests/test_searxng_runtime.py
git commit -m "feat(searxng): sidecar runtime manager (spawn, health, reuse, stop)"
```

---

### Task 4: Setup script

**Files:**
- Create: `scripts/setup-searxng.sh` (chmod +x)

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Install the managed SearXNG sidecar into data/searxng/ — no Docker.
# Idempotent: re-running updates the checkout and reuses venv/settings.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$ROOT/data/searxng"
SRC="$HOME_DIR/src"
VENV="$HOME_DIR/venv"
PORT="${SEARXNG_PORT:-8893}"

mkdir -p "$HOME_DIR"

echo "==> Fetching SearXNG source"
if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 https://github.com/searxng/searxng "$SRC"
else
  git -C "$SRC" pull --ff-only || echo "(pull failed — keeping existing checkout)"
fi

echo "==> Creating venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -U pip setuptools wheel

echo "==> Installing SearXNG (this can take a few minutes)"
"$VENV/bin/pip" install -q --use-pep517 "$SRC"

if [ ! -f "$HOME_DIR/settings.yml" ]; then
  echo "==> Writing settings.yml"
  SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$HOME_DIR/settings.yml" <<EOF
# Generated by scripts/setup-searxng.sh — localhost-only, JSON API enabled.
use_default_settings: true
server:
  secret_key: "$SECRET"
  bind_address: "127.0.0.1"
  port: $PORT
  limiter: false
  public_instance: false
search:
  formats:
    - html
    - json
EOF
fi

echo "==> Done. Apollo will start the sidecar automatically (searxng_managed=true)."
echo "    Manual start: SEARXNG_SETTINGS_PATH='$HOME_DIR/settings.yml' '$VENV/bin/python' -m searx.webapp"
```

- [ ] **Step 2: Make executable and smoke-test the install**

```bash
chmod +x scripts/setup-searxng.sh
./scripts/setup-searxng.sh
```
Expected: ends with `==> Done.` and `data/searxng/{src,venv,settings.yml}` exist.

- [ ] **Step 3: Smoke-test the server boots and serves JSON**

```bash
SEARXNG_SETTINGS_PATH="$PWD/data/searxng/settings.yml" data/searxng/venv/bin/python -m searx.webapp &
SXPID=$!
sleep 6
curl -s "http://127.0.0.1:8893/healthz"; echo
curl -s "http://127.0.0.1:8893/search?q=test&format=json" | head -c 200; echo
kill $SXPID
```
Expected: `OK` from healthz; JSON body (starts with `{"query": "test"`) from search. If the JSON call returns 403, the settings.yml `formats` block didn't apply — confirm `SEARXNG_SETTINGS_PATH` points at the generated file.

- [ ] **Step 4: Ensure data/searxng is gitignored**

```bash
grep -q "^data/" .gitignore || echo "data/searxng/" >> .gitignore
git check-ignore data/searxng/venv && echo IGNORED
```
Expected: `IGNORED` (the repo already ignores `data/`; only add the narrower line if it doesn't).

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-searxng.sh .gitignore
git commit -m "feat(searxng): no-Docker setup script (venv install + localhost settings.yml)"
```

---

### Task 5: App startup/shutdown wiring

**Files:**
- Modify: `app.py` (after the Paperclip runtime block, around line 858)

- [ ] **Step 1: Add startup/shutdown hooks**

In `app.py`, directly after the `_stop_paperclip_runtime` function (ends near line 857), add:

```python
# Managed SearXNG sidecar (no Docker): start if installed+enabled; reuse an
# already-running instance. Skipped entirely when not installed — search then
# falls back to DuckDuckGo via the provider chain.
from services.searxng.runtime import get_runtime as _get_searxng_runtime


@app.on_event("startup")
async def _start_searxng_runtime():
    def _boot():
        try:
            _get_searxng_runtime().start()
        except Exception as e:
            logger.warning("SearXNG sidecar startup failed (non-critical): %s", e)
    threading.Thread(target=_boot, name="searxng-runtime", daemon=True).start()


@app.on_event("shutdown")
async def _stop_searxng_runtime():
    try:
        _get_searxng_runtime().stop()
    except Exception:
        pass
```

- [ ] **Step 2: Verify the app still boots**

Run: `venv/bin/python -c "import app" 2>&1 | tail -3`
Expected: no traceback (import-time wiring is sound).

- [ ] **Step 3: Run the full test suite for regressions**

Run: `venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -5`
Expected: no new failures (note any pre-existing failures before this task).

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(searxng): start/stop managed sidecar with the app"
```

---

### Task 6: Immediate DuckDuckGo fallback + managed instance URL + tight timeouts

**Files:**
- Modify: `services/search/providers.py` (`_get_search_instance` ~line 43, `searxng_search_api` timeout ~line 188, `searxng_search` timeout ~line 258)
- Modify: `services/search/core.py` (`_build_provider_chain` ~line 95)
- Test: `tests/test_search_immediate_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
"""Provider chain skips SearXNG instantly when the managed sidecar is down."""
from unittest.mock import patch

from services.search.core import _build_provider_chain


def _settings(**over):
    base = {
        "search_provider": "searxng",
        "search_fallback_chain": ["duckduckgo"],
        "search_url": "",
        "searxng_managed": True,
    }
    base.update(over)
    return base


def test_skips_searxng_when_sidecar_down():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.is_serving.return_value = False
        chain = _build_provider_chain("searxng")
    assert chain == ["duckduckgo"]


def test_keeps_searxng_when_sidecar_running():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime") as rt:
        rt.return_value.is_serving.return_value = True
        chain = _build_provider_chain("searxng")
    assert chain == ["searxng", "duckduckgo"]


def test_keeps_searxng_with_custom_url():
    # User points at their own external instance — never skip on its behalf.
    with patch("services.search.core._get_search_settings",
               return_value=_settings(search_url="http://my-searx.lan:8080")):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"


def test_keeps_searxng_when_not_managed():
    with patch("services.search.core._get_search_settings",
               return_value=_settings(searxng_managed=False)):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"


def test_runtime_errors_fail_open():
    with patch("services.search.core._get_search_settings", return_value=_settings()), \
         patch("services.searxng.runtime.get_runtime", side_effect=RuntimeError("boom")):
        chain = _build_provider_chain("searxng")
    assert chain[0] == "searxng"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_search_immediate_fallback.py -v`
Expected: `test_skips_searxng_when_sidecar_down` FAILS (chain still contains "searxng")

- [ ] **Step 3: Implement the skip in `services/search/core.py`**

Replace `_build_provider_chain` (lines 95–106) with:

```python
def _searxng_definitely_down() -> bool:
    """True only when the MANAGED sidecar is the target and it isn't serving.

    Never true for a custom search_url (external instance) or when the
    managed sidecar is disabled — those cases let the request itself decide.
    """
    settings = _get_search_settings()
    if (settings.get("search_url") or "").strip():
        return False
    if not settings.get("searxng_managed", True):
        return False
    try:
        from services.searxng.runtime import get_runtime
        return not get_runtime().is_serving()
    except Exception:
        return False  # fail open — let the HTTP call decide


def _build_provider_chain(primary: str) -> List[str]:
    """Build ordered list: primary first, then configured/default fallbacks.

    When the primary is the managed SearXNG sidecar and it's down/not
    installed, skip it entirely so the fallback (DuckDuckGo) answers with
    no timeout penalty.
    """
    chain = [primary]
    if primary == "searxng" and _searxng_definitely_down():
        logger.info("SearXNG sidecar not serving — skipping straight to fallback providers")
        chain = []
    settings = _get_search_settings()
    user_chain = settings.get("search_fallback_chain") or []
    if isinstance(user_chain, str):
        user_chain = [s.strip() for s in user_chain.split(",") if s.strip()]
    fallbacks = user_chain if user_chain else _FALLBACK_ORDER
    for fb in fallbacks:
        if fb and fb != primary and fb not in chain and fb != "disabled":
            chain.append(fb)
    if not chain:
        chain = list(_FALLBACK_ORDER)
    return chain
```

- [ ] **Step 4: Point the default instance URL at the managed sidecar**

In `services/search/providers.py`, replace `_get_search_instance` (lines 43–49) with:

```python
def _get_search_instance() -> str:
    """Return the active search API URL: explicit setting > managed sidecar > env."""
    settings = _get_search_settings()
    url = (settings.get("search_url") or "").strip()
    if url:
        return url.rstrip("/")
    if settings.get("searxng_managed", True):
        try:
            from services.searxng.runtime import get_runtime
            return get_runtime().url
        except Exception:
            pass
    return SEARXNG_INSTANCE
```

- [ ] **Step 5: Tighten connect timeouts on the SearXNG calls**

In `services/search/providers.py`:
- `searxng_search_api`, the `_run` helper (~line 188): change `timeout=15,` to `timeout=httpx.Timeout(15.0, connect=2.0),`
- `searxng_search` HTML fallback (~line 262): change `timeout=10,` to `timeout=httpx.Timeout(10.0, connect=2.0),`

(`httpx` is already imported at the top of the file.)

- [ ] **Step 6: Run tests**

Run: `venv/bin/python -m pytest tests/test_search_immediate_fallback.py tests/ -k "search" -q 2>&1 | tail -5`
Expected: new tests pass; no regressions in existing search tests.

- [ ] **Step 7: Commit**

```bash
git add services/search/core.py services/search/providers.py tests/test_search_immediate_fallback.py
git commit -m "feat(search): immediate DuckDuckGo fallback when managed SearXNG is down; sidecar URL default; 2s connect timeouts"
```

---

### Task 7: Provider attribution in sources (for the "via DuckDuckGo" badge)

**Files:**
- Modify: `services/search/core.py` (`comprehensive_web_search`, the source-list build ~line 330)
- Test: `tests/test_search_provider_attribution.py`

- [ ] **Step 1: Write the failing test**

```python
"""comprehensive_web_search tags sources with the provider that answered."""
from unittest.mock import patch

from services.search.core import comprehensive_web_search


def test_sources_carry_provider(tmp_path):
    fake_results = [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]
    with patch("services.search.core._get_search_settings",
               return_value={"search_provider": "searxng",
                             "search_fallback_chain": ["duckduckgo"],
                             "search_url": "", "searxng_managed": False,
                             "search_result_count": 5}), \
         patch("services.search.core._call_provider",
               side_effect=lambda name, q, c, tf: fake_results if name == "duckduckgo" else []), \
         patch("services.search.core.rank_search_results", side_effect=lambda q, r: r), \
         patch("services.search.core.fetch_webpage_content",
               return_value={"success": False, "content": "", "url": "", "title": ""}):
        _text, sources = comprehensive_web_search("query", return_sources=True)
    assert sources[0]["provider"] == "duckduckgo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_search_provider_attribution.py -v`
Expected: FAIL with `KeyError: 'provider'`

- [ ] **Step 3: Implement**

In `services/search/core.py`, `comprehensive_web_search`: the provider loop already tracks `provider_name`. Capture the winner right after the loop (after the `if not search_results:` block, before `rank_search_results`):

```python
    winning_provider = provider_name  # provider that produced search_results
```

Then change the `_source_list` build (~line 330) from:

```python
    _source_list = [
        {"url": r.get("url", ""), "title": r.get("title", "")}
        for r in search_results if r.get("url")
    ]
```

to:

```python
    _source_list = [
        {"url": r.get("url", ""), "title": r.get("title", ""), "provider": winning_provider}
        for r in search_results if r.get("url")
    ]
```

Also add a fallback-visibility log right after `winning_provider` is set:

```python
    settings_provider = search_provider
    if winning_provider != settings_provider:
        logger.info("Search answered via fallback provider %s (primary: %s)",
                    winning_provider, settings_provider)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_search_provider_attribution.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/search/core.py tests/test_search_provider_attribution.py
git commit -m "feat(search): tag web sources with answering provider + fallback log line"
```

---

### Task 8: SearXNG status/install API

**Files:**
- Modify: `routes/search_routes.py`
- Test: `tests/test_searxng_routes.py`

First check the admin-gate idiom used in this repo: `grep -n "require_admin" routes/settings_routes.py core/auth.py | head -5` — use the same import/call style in the new endpoints (it is `from core.auth import require_admin` and a call at the top of the handler, matching settings routes).

- [ ] **Step 1: Write the failing test**

```python
"""SearXNG sidecar status/install endpoints."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.search_routes import setup_search_routes


def _client():
    app = FastAPI()
    app.include_router(setup_search_routes(config={}))
    return TestClient(app)


def test_status_reports_runtime():
    with patch("services.searxng.runtime.get_runtime") as rt, \
         patch("routes.search_routes.require_admin", return_value=None):
        rt.return_value.status.return_value = "running"
        rt.return_value.url = "http://127.0.0.1:8893"
        res = _client().get("/api/search/searxng/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert body["url"] == "http://127.0.0.1:8893"
    assert body["installing"] is False


def test_install_kicks_off_script():
    started = {}

    def fake_thread(target=None, **kw):
        class T:
            def start(self_inner):
                started["yes"] = True
        return T()

    with patch("routes.search_routes.require_admin", return_value=None), \
         patch("routes.search_routes.threading.Thread", side_effect=fake_thread):
        res = _client().post("/api/search/searxng/install")
    assert res.status_code == 200
    assert res.json()["started"] is True
    assert started.get("yes") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_searxng_routes.py -v`
Expected: FAIL with 404 (routes don't exist)

- [ ] **Step 3: Implement**

In `routes/search_routes.py`, add at the top (matching existing imports):

```python
import os
import subprocess
import threading

from core.auth import require_admin
from src.constants import BASE_DIR
```

Inside `setup_search_routes`, before `return router`, add:

```python
    # ── Managed SearXNG sidecar ──
    _install_state = {"running": False, "log": [], "ok": None}

    @router.get("/api/search/searxng/status")
    async def searxng_status(request: Request):
        require_admin(request)
        from services.searxng.runtime import get_runtime
        rt = get_runtime()
        return {
            "status": rt.status(),
            "url": rt.url,
            "installing": _install_state["running"],
            "install_ok": _install_state["ok"],
            "log_tail": _install_state["log"][-20:],
        }

    @router.post("/api/search/searxng/install")
    async def searxng_install(request: Request):
        require_admin(request)
        if _install_state["running"]:
            return {"started": False, "reason": "already running"}
        _install_state.update(running=True, log=[], ok=None)

        def _run():
            script = os.path.join(BASE_DIR, "scripts", "setup-searxng.sh")
            try:
                proc = subprocess.Popen(
                    ["bash", script],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for line in proc.stdout:
                    _install_state["log"].append(line.rstrip())
                ok = proc.wait() == 0
                _install_state["ok"] = ok
                if ok:
                    from services.searxng.runtime import get_runtime
                    get_runtime().start()
            except Exception as e:
                _install_state["log"].append(f"install failed: {e}")
                _install_state["ok"] = False
            finally:
                _install_state["running"] = False

        threading.Thread(target=_run, name="searxng-install", daemon=True).start()
        return {"started": True}
```

If `require_admin(request)` in this repo is a dependency rather than a callable (check the grep from the task intro), follow the settings-routes idiom exactly instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_searxng_routes.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add routes/search_routes.py tests/test_searxng_routes.py
git commit -m "feat(searxng): admin status/install endpoints for the managed sidecar"
```

---

### Task 9: Web decider — heuristics

**Files:**
- Create: `src/web_decider.py`
- Test: `tests/test_web_decider.py`

- [ ] **Step 1: Write the failing test**

```python
"""Heuristic web-need classification for web_access=auto."""
import pytest

from src.web_decider import heuristic_decision


@pytest.mark.parametrize("msg", [
    "What's the latest news on the EU AI Act?",
    "Search for reviews of the Framework 16 laptop",
    "current price of AMD stock",
    "weather in Stockholm today",
    "When is the next SpaceX launch scheduled?",
    "look up the Python 3.14 release notes",
])
def test_clear_yes(msg):
    assert heuristic_decision(msg) == "yes"


@pytest.mark.parametrize("msg", [
    "Write me a poem about autumn",
    "Refactor this function to use a dict",
    "Translate 'good morning' to Swedish",
    "def f(x):\n    return x + 1\n```python\nfix this\n```",
    "Summarize the following text: Lorem ipsum dolor",
    "https://example.com/article — what does this say?",  # URL: auto-fetch handles it
])
def test_clear_no(msg):
    assert heuristic_decision(msg) == "no"


@pytest.mark.parametrize("msg", [
    "Who is the CEO of Anthropic?",
    "How many people live in Reykjavik?",
])
def test_ambiguous(msg):
    assert heuristic_decision(msg) == "ambiguous"


def test_empty_is_no():
    assert heuristic_decision("") == "no"
    assert heuristic_decision(None) == "no"


def test_long_paste_is_no():
    assert heuristic_decision("latest news " + "x" * 4000) == "no"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_web_decider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.web_decider'`

- [ ] **Step 3: Implement**

`src/web_decider.py`:

```python
"""Decide whether a chat message needs live web results (web_access=auto).

Two stages:
1. heuristic_decision(message) — instant regex pass: 'yes' | 'no' | 'ambiguous'
2. decide_use_web(message)     — async; breaks 'ambiguous' ties with a one-token
   utility-model call IF a separate utility endpoint is configured. Never
   targets the warm local chat model (would force a llama.cpp model swap).

resolve_web_access() maps the client's tri-state web_access param
('off'|'auto'|'always', falling back to the web_access_mode setting) onto the
legacy use_web / allow_web_search flags the chat pipeline already understands.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Explicit asks — always search.
_FORCE_RE = re.compile(
    r"\b(search( the web)?( for)?|look up|google|web search)\b", re.I)

# Freshness / volatile-fact signals.
_RECENCY_RE = re.compile(
    r"\b(today|tonight|yesterday|this (week|month|year)|latest|current(ly)?|"
    r"breaking|news|headlines?|price|stock|weather|forecast|score|schedule[d]?|"
    r"release (date|notes)|just (released|announced)|right now|upcoming|next "
    r"(launch|release|election|game|match))\b", re.I)

# Self-contained work — the answer is in the message or the model's weights.
_NO_WEB_RE = re.compile(
    r"\b(refactor|rewrite|fix (this|my|the)|debug|translate|"
    r"write( me)? a (poem|story|song|haiku|letter|script)|"
    r"summari[sz]e (this|the following)|explain (this|the following))\b", re.I)

_URL_RE = re.compile(r"https?://", re.I)

_QUESTION_WORDS = ("who", "what", "when", "where", "which", "how much", "how many")


def heuristic_decision(message: Optional[str]) -> str:
    """Classify a message: 'yes' (search), 'no' (skip), 'ambiguous'."""
    msg = (message or "").strip()
    if not msg:
        return "no"
    if "```" in msg or len(msg) > 4000:
        return "no"  # pasted code/content — answer from what's provided
    if _URL_RE.search(msg):
        return "no"  # embedded URLs are auto-fetched by chat_processor
    if _NO_WEB_RE.search(msg):
        return "no"
    if _FORCE_RE.search(msg):
        return "yes"
    if _RECENCY_RE.search(msg):
        return "yes"
    lower = msg.lower()
    if msg.rstrip().endswith("?") and any(lower.startswith(w) or f" {w} " in lower
                                          for w in _QUESTION_WORDS):
        return "ambiguous"  # factual question — may be answerable from weights
    return "no"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_web_decider.py -v`
Expected: all passed. If a parametrized case misclassifies, adjust the regex — the word lists are the spec'd starting point, not sacred.

- [ ] **Step 5: Commit**

```bash
git add src/web_decider.py tests/test_web_decider.py
git commit -m "feat(chat): heuristic web-need classifier for auto web access"
```

---

### Task 10: Web decider — utility tie-break + resolve_web_access

**Files:**
- Modify: `src/web_decider.py`
- Test: `tests/test_web_decider_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
"""resolve_web_access maps tri-state mode onto use_web/allow_web_search."""
from unittest.mock import AsyncMock, patch

import pytest

from src.web_decider import decide_use_web, resolve_web_access


@pytest.mark.asyncio
async def test_manual_mode_passes_through(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "manual"})
    use_web, allow_ws, decision = await resolve_web_access(
        None, "chat", "hello", "true", None)
    assert use_web == "true"          # untouched legacy flag
    assert allow_ws is None
    assert decision is None


@pytest.mark.asyncio
async def test_off_disables_everything():
    use_web, allow_ws, decision = await resolve_web_access(
        "off", "agent", "latest news", "true", "true")
    assert use_web is False
    assert allow_ws == "false"
    assert decision == "off"


@pytest.mark.asyncio
async def test_always_chat_sets_use_web():
    use_web, allow_ws, _ = await resolve_web_access(
        "always", "chat", "hello", None, None)
    assert use_web is True


@pytest.mark.asyncio
async def test_always_agent_enables_tools():
    use_web, allow_ws, _ = await resolve_web_access(
        "always", "agent", "hello", None, None)
    assert allow_ws == "true"


@pytest.mark.asyncio
async def test_auto_agent_enables_tools_without_presearch():
    use_web, allow_ws, decision = await resolve_web_access(
        "auto", "agent", "write a poem", None, None)
    assert allow_ws == "true"
    assert decision == "auto-tools"


@pytest.mark.asyncio
async def test_auto_chat_searches_when_decider_says_yes():
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "latest news", None, None)
    assert use_web is True
    assert decision == "auto-search"


@pytest.mark.asyncio
async def test_auto_chat_skips_when_decider_says_no():
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=False)):
        use_web, _, decision = await resolve_web_access(
            "auto", "chat", "write a poem", None, None)
    assert use_web is False
    assert decision == "auto-skip"


@pytest.mark.asyncio
async def test_settings_auto_applies_when_param_missing(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"web_access_mode": "auto"})
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, _, decision = await resolve_web_access(
            None, "chat", "latest news", None, None)
    assert use_web is True


@pytest.mark.asyncio
async def test_decide_yes_no_skip_utility():
    # Clear heuristic verdicts never call the utility model.
    with patch("src.web_decider._ask_utility_model", new=AsyncMock()) as ask:
        assert await decide_use_web("latest news on rust") is True
        assert await decide_use_web("write a poem") is False
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_decide_ambiguous_uses_utility_when_configured(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": "ep1"})
    with patch("src.web_decider._ask_utility_model", new=AsyncMock(return_value=True)):
        assert await decide_use_web("Who is the CEO of Anthropic?") is True


@pytest.mark.asyncio
async def test_decide_ambiguous_defaults_no_without_utility(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings",
                        lambda: {"utility_endpoint_id": ""})
    assert await decide_use_web("Who is the CEO of Anthropic?") is False
```

Check `pytest-asyncio` is available: `venv/bin/python -c "import pytest_asyncio"` — if missing, mirror however existing async tests in `tests/` run (grep `asyncio` in `tests/conftest.py`; use the same mechanism).

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_web_decider_resolve.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_web_access'`

- [ ] **Step 3: Implement**

Append to `src/web_decider.py`:

```python
async def _ask_utility_model(message: str) -> Optional[bool]:
    """One-token YES/NO from the utility model. None on any failure.

    Only called when utility_endpoint_id is explicitly set — that endpoint is
    always-on (e.g. Ollama) and separate from the single warm llama.cpp slot,
    so this never forces a local model swap.
    """
    try:
        import httpx
        from src.endpoint_resolver import resolve_endpoint, build_chat_url, build_headers

        resolved = resolve_endpoint(setting_prefix="utility")
        if not resolved:
            return None
        base, model, api_key = resolved[0], resolved[1], (resolved[2] if len(resolved) > 2 else None)
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": ("Does answering this message require up-to-date "
                            "information from the public web (news, prices, "
                            "schedules, recent events, current facts)? "
                            "Answer with exactly YES or NO.\n\n"
                            f"Message: {message[:500]}"),
            }],
            "max_tokens": 3,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.post(build_chat_url(base), json=payload,
                                  headers=build_headers(api_key, base))
            r.raise_for_status()
            text = (r.json()["choices"][0]["message"]["content"] or "").strip().upper()
        if text.startswith("YES"):
            return True
        if text.startswith("NO"):
            return False
        return None
    except Exception as e:
        logger.debug("utility web-decider call failed: %s", e)
        return None
```

**Adapt the `resolve_endpoint` call to its real signature** — read `src/endpoint_resolver.py:205` first; it returns a structured value (check whether it's a tuple or object and what order). The test mocks `_ask_utility_model` entirely, so only the live path depends on this; get it right by reading the function, not guessing.

```python
async def decide_use_web(message: str) -> bool:
    verdict = heuristic_decision(message)
    if verdict == "yes":
        return True
    if verdict == "no":
        return False
    # ambiguous — tie-break with the utility model when one is configured
    from src.settings import load_settings
    if (load_settings().get("utility_endpoint_id") or "").strip():
        answer = await _ask_utility_model(message)
        if answer is not None:
            return answer
    return False  # conservative default: no extra latency/noise


async def resolve_web_access(
    web_access: Optional[str],
    chat_mode: str,
    message: str,
    use_web,
    allow_web_search,
) -> Tuple[object, object, Optional[str]]:
    """Map the tri-state web_access onto (use_web, allow_web_search, decision).

    decision is None when legacy manual behavior applies (flags untouched),
    otherwise one of: 'off', 'always', 'auto-tools', 'auto-search', 'auto-skip'.
    """
    mode = (web_access or "").strip().lower()
    if mode not in ("off", "auto", "always"):
        from src.settings import load_settings
        cfg = (load_settings().get("web_access_mode") or "manual").strip().lower()
        if cfg not in ("auto", "always"):
            return use_web, allow_web_search, None
        mode = cfg
    if mode == "off":
        return False, "false", "off"
    if mode == "always":
        if chat_mode == "agent":
            return use_web, "true", "always"
        return True, allow_web_search, "always"
    # auto
    if chat_mode == "agent":
        # Tools are available; the model decides per call. No forced pre-search.
        return use_web, "true", "auto-tools"
    needed = await decide_use_web(message or "")
    return needed, allow_web_search, ("auto-search" if needed else "auto-skip")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_web_decider_resolve.py tests/test_web_decider.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/web_decider.py tests/test_web_decider_resolve.py
git commit -m "feat(chat): resolve_web_access tri-state + utility-model tie-break"
```

---

### Task 11: Wire the decider into the chat routes

**Files:**
- Modify: `routes/chat_routes.py` (streaming handler ~line 373–480; non-streaming handler ~line 250–296)
- Modify: `src/request_models.py` (ChatRequest, line 7)
- Test: `tests/test_chat_web_access_wiring.py`

- [ ] **Step 1: Add `web_access` to ChatRequest**

In `src/request_models.py`, inside `ChatRequest` after the `use_web` field:

```python
    web_access: Optional[str] = Field(
        default=None, description="Web access mode: off | auto | always")
```

- [ ] **Step 2: Wire the streaming endpoint**

In `routes/chat_routes.py`, after line 378 (`allow_web_search = form_data.get("allow_web_search")`), add:

```python
        web_access = form_data.get("web_access")
```

Then, after the mode persist block (after `set_session_mode(session, _effective_mode)`, ~line 460) and **before** `build_chat_context` (~line 474), add:

```python
        # Tri-state web access (off/auto/always). 'auto' runs the decider for
        # chat mode and enables web tools for agent mode. Legacy clients that
        # don't send web_access keep the old use_web/allow_web_search behavior.
        from src.web_decider import resolve_web_access
        use_web, allow_web_search, _web_decision = await resolve_web_access(
            web_access, chat_mode, message if isinstance(message, str) else "",
            use_web, allow_web_search,
        )
        if _web_decision:
            logger.info("web_access decision=%s session=%s", _web_decision, session)
```

- [ ] **Step 3: Wire the non-streaming endpoint**

In the `chat_endpoint` handler (~line 250), after `use_web = chat_request.use_web` and before `build_chat_context` (~line 290), add:

```python
        from src.web_decider import resolve_web_access
        use_web, _ignored_allow_ws, _web_decision = await resolve_web_access(
            chat_request.web_access, "chat", message, use_web, None,
        )
        if _web_decision:
            logger.info("web_access decision=%s session=%s", _web_decision, session)
```

- [ ] **Step 4: Write the integration test**

`tests/test_chat_web_access_wiring.py` — tests `resolve_web_access` is honored at the route level by exercising the decision logic with the route's exact call shape (full route tests need the app fixture; reuse whatever `tests/conftest.py` provides — check for an existing TestClient/app fixture and follow a neighboring chat route test's setup, e.g. `grep -l "chat_stream" tests/*.py`):

```python
"""web_access param reaches resolve_web_access with the route's call shape."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_route_call_shape_auto_chat():
    from src.web_decider import resolve_web_access
    with patch("src.web_decider.decide_use_web", new=AsyncMock(return_value=True)):
        use_web, allow_ws, decision = await resolve_web_access(
            "auto", "chat", "what's the latest python release?", None, None)
    assert use_web is True and decision == "auto-search"


@pytest.mark.asyncio
async def test_route_call_shape_form_strings():
    # Form values arrive as strings or None — must not crash.
    from src.web_decider import resolve_web_access
    use_web, allow_ws, decision = await resolve_web_access(
        "always", "agent", "hello", "true", None)
    assert allow_ws == "true"
```

If `tests/conftest.py` exposes a full-app TestClient fixture, ADD one end-to-end test posting to `/api/chat_stream` with `web_access=off` and asserting no search call is made (`patch("src.chat_processor.comprehensive_web_search")` → `assert_not_called`). If no such fixture exists, the unit coverage above plus Task 14's manual verification covers it — do not build a new app fixture for this.

- [ ] **Step 5: Run tests**

Run: `venv/bin/python -m pytest tests/test_chat_web_access_wiring.py -v && venv/bin/python -m pytest tests/ -q -k "chat" 2>&1 | tail -5`
Expected: new tests pass, no chat-route regressions

- [ ] **Step 6: Commit**

```bash
git add routes/chat_routes.py src/request_models.py tests/test_chat_web_access_wiring.py
git commit -m "feat(chat): wire tri-state web_access through both chat endpoints"
```

---

### Task 12: Frontend — tri-state web toggle

**Files:**
- Modify: `static/app.js` (`MODE_TOOLS`/`setupToggle` block, lines 1559–1695; `applyModeToToggles` line 1592)
- Modify: `static/js/chat.js` (send flags lines 744–763; spinner texts lines 809, 837)
- Modify: the main stylesheet (find it: `grep -rln "input-icon-btn" static/*.css static/css/*.css` — add the rule to that file)

No JS unit-test harness covers app.js; verification is manual (Step 5) plus the backend tests already covering the param.

- [ ] **Step 1: Add web-mode helpers and a cycling toggle in `static/app.js`**

Below `saveToolPref` (line 1579), add:

```javascript
  // ── Tri-state web access (off → auto → always) ──
  const WEB_MODES = ['off', 'auto', 'always'];

  function loadWebMode(mode) {
    const state = loadToggleState();
    const key = 'webmode_' + mode;
    if (WEB_MODES.includes(state[key])) return state[key];
    // Migrate legacy boolean web_<mode> prefs: true→always, false→off
    const legacyKey = 'web_' + mode;
    if (Object.prototype.hasOwnProperty.call(state, legacyKey)) {
      return state[legacyKey] ? 'always' : 'off';
    }
    return 'auto'; // new default: let the server decide per message
  }

  function saveWebMode(mode, value) {
    const state = loadToggleState();
    state['webmode_' + mode] = value;
    saveToggleState(state);
  }

  function applyWebModeToButton(webMode) {
    const btn = el('web-toggle-btn');
    const chk = el('web-toggle');
    if (!btn) return;
    btn.classList.toggle('active', webMode !== 'off');
    btn.classList.toggle('web-auto', webMode === 'auto');
    btn.setAttribute('aria-pressed', String(webMode !== 'off'));
    btn.title = 'Web search: ' + webMode;
    if (chk) chk.checked = webMode !== 'off'; // compat: compare mode, slash cmds
  }
```

Replace the line `setupToggle('web-toggle-btn', 'web-toggle', 'web');` (line 1695) with:

```javascript
  // Web toggle cycles off → auto → always (not a plain checkbox toggle).
  (function setupWebToggle() {
    const btn = el('web-toggle-btn');
    if (!btn) return;
    const mode = (loadToggleState().mode) || 'chat';
    applyWebModeToButton(loadWebMode(mode));
    btn.addEventListener('click', () => {
      const curMode = (loadToggleState().mode) || 'chat';
      const cur = loadWebMode(curMode);
      const next = WEB_MODES[(WEB_MODES.indexOf(cur) + 1) % WEB_MODES.length];
      saveWebMode(curMode, next);
      applyWebModeToButton(next);
      if (uiModule?.showToast) uiModule.showToast('Web search: ' + next, 1800);
      if (next !== 'off') _showToolSplash('web');
      if (next !== 'off') {
        const resChk = el('research-toggle');
        if (resChk && resChk.checked) _syncResearchIndicator(false);
      }
    });
  })();
```

In `applyModeToToggles` (line 1592), the `MODE_TOOLS` loop still handles `bash`; remove the web entry from `MODE_TOOLS` (line 1562) and add at the end of `applyModeToToggles`:

```javascript
    applyWebModeToButton(loadWebMode(mode));
```

- [ ] **Step 2: Send the mode from `static/js/chat.js`**

Replace the block at lines 752–758:

```javascript
      if (el('web-toggle').checked) {
        if (isAgentMode) {
          fd.append('allow_web_search', 'true');
        } else {
          fd.append('use_web', 'true');
        }
      }
```

with:

```javascript
      const _webMode = (() => {
        const st = Storage.loadToggleState();
        const key = 'webmode_' + (isAgentMode ? 'agent' : 'chat');
        if (['off', 'auto', 'always'].includes(st[key])) return st[key];
        const legacy = st['web_' + (isAgentMode ? 'agent' : 'chat')];
        if (legacy !== undefined) return legacy ? 'always' : 'off';
        return 'auto';
      })();
      fd.append('web_access', _webMode);
      if (_webMode === 'always') {
        // Keep legacy flags so older server code paths behave identically.
        if (isAgentMode) fd.append('allow_web_search', 'true');
        else fd.append('use_web', 'true');
      }
```

- [ ] **Step 3: Spinner texts** — at lines 809 and 837, change the condition `el('web-toggle').checked && !_isAgent` to `_webMode === 'always' && !_isAgent` (the `_webMode` const from Step 2 is in the same function scope — verify, otherwise hoist it above the first use). Auto mode keeps the generic "Processing request..." (search may or may not fire).

- [ ] **Step 4: CSS for the auto state** — in the stylesheet found above, near other `.input-icon-btn` rules, add:

```css
/* Web toggle in 'auto': lit but with a small A badge to distinguish from always-on */
#web-toggle-btn.web-auto { opacity: 0.85; }
#web-toggle-btn.web-auto::after {
  content: 'A';
  position: absolute;
  font-size: 8px;
  font-weight: 700;
  bottom: 1px;
  right: 2px;
}
```

(`.input-icon-btn` may need `position: relative;` — add it to the `#web-toggle-btn` rule if not already set globally.)

- [ ] **Step 5: Manual verification**

Start the app (`./start-macos.sh` or `venv/bin/uvicorn app:app --port 7000`), open the UI:
1. Click the magnifying-glass button repeatedly → toast cycles "Web search: off → auto → always", button shows the A badge in auto.
2. In chat mode, auto, send "What's the latest news about NASA?" → server log shows `web_access decision=auto-search`, reply cites sources.
3. Send "Write a haiku about rain" → log shows `auto-skip`, no search delay.
4. Reload the page → mode persisted per chat/agent mode.

- [ ] **Step 6: Commit**

```bash
git add static/app.js static/js/chat.js static/<stylesheet>
git commit -m "feat(ui): tri-state web access toggle (off/auto/always) wired to web_access param"
```

---

### Task 13: Settings UI — sidecar status + web access default

**Files:**
- Modify: `static/js/settings.js` (search section, around lines 1390–1460)
- Modify: `static/index.html` (search settings markup, around line 1540)

- [ ] **Step 1: Locate the exact markup**

Run: `grep -n "search_provider\|search-provider" static/index.html | head` and read the surrounding block — the new rows go directly under the provider dropdown, matching the existing row markup classes.

- [ ] **Step 2: Add the two rows to `static/index.html`** (adapt class names to the neighboring rows — copy a sibling row's structure exactly):

```html
<!-- Managed SearXNG sidecar -->
<div class="settings-row" id="searxng-sidecar-row">
  <label>SearXNG sidecar</label>
  <span id="searxng-sidecar-status">checking…</span>
  <button type="button" id="searxng-install-btn" class="btn-small">Install</button>
</div>
<!-- Default web access mode -->
<div class="settings-row">
  <label for="web-access-mode">Web access default</label>
  <select id="web-access-mode">
    <option value="manual">Manual (toggle only)</option>
    <option value="auto">Auto (search when needed)</option>
    <option value="always">Always search</option>
  </select>
</div>
```

- [ ] **Step 3: Wire in `static/js/settings.js`** — next to the existing search-settings load (line ~1390) add:

```javascript
  // ── SearXNG sidecar status / install ──
  async function refreshSearxngStatus() {
    const span = document.getElementById('searxng-sidecar-status');
    const btn = document.getElementById('searxng-install-btn');
    if (!span) return;
    try {
      const res = await fetch(API_BASE + '/api/search/searxng/status', { credentials: 'same-origin' });
      const s = await res.json();
      const labels = {
        running: '● running at ' + s.url,
        not_installed: '○ not installed',
        stopped: '○ installed, not running',
        failed: '✕ failed to start',
        disabled: '— disabled',
      };
      span.textContent = s.installing ? '⟳ installing…' : (labels[s.status] || s.status);
      if (btn) {
        btn.textContent = s.status === 'not_installed' ? 'Install' : 'Update';
        btn.disabled = !!s.installing;
      }
      if (s.installing) setTimeout(refreshSearxngStatus, 3000);
    } catch (_e) {
      span.textContent = 'status unavailable';
    }
  }
  document.getElementById('searxng-install-btn')?.addEventListener('click', async () => {
    await fetch(API_BASE + '/api/search/searxng/install', { method: 'POST', credentials: 'same-origin' });
    refreshSearxngStatus();
  });
  refreshSearxngStatus();

  // ── Web access default ──
  const webAccessSel = document.getElementById('web-access-mode');
  if (webAccessSel) {
    if (_settings.web_access_mode) webAccessSel.value = _settings.web_access_mode;
    webAccessSel.addEventListener('change', () => {
      // Mirror the save pattern used by search_fallback_chain (~line 1594)
      saveSearchSetting({ web_access_mode: webAccessSel.value });
    });
  }
```

`saveSearchSetting` is a stand-in: copy the exact fetch call used at line ~1594 (`body: JSON.stringify({ search_fallback_chain: chain })`) including its endpoint and headers, and send `{ web_access_mode: ... }` the same way. Also confirm `API_BASE`/`_settings` are in scope at the insertion point (they are used at line 1390).

- [ ] **Step 4: Manual verification** — Settings → Search shows the sidecar row; clicking Install starts the script (watch `data/searxng/` appear and status flip to running); the Web access default dropdown persists across reloads (check `data/settings.json` contains `"web_access_mode"`).

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/js/settings.js
git commit -m "feat(ui): SearXNG sidecar status/install + web access default in Settings"
```

---

### Task 14: Enablement + end-to-end verification (with the user)

**Files:** none (configuration + verification)

- [ ] **Step 1: Enable Deep Research feature flag** (user-data change, not a code default change):

```bash
venv/bin/python - <<'EOF'
import json
p = "data/features.json"
try:
    d = json.load(open(p))
except Exception:
    d = {}
d["deep_research"] = True
json.dump(d, open(p, "w"), indent=2)
print(json.load(open(p)))
EOF
```

- [ ] **Step 2: Verify crawl4ai** (already in requirements.txt:47):

```bash
venv/bin/python -c "import crawl4ai; print(crawl4ai.__version__)" || venv/bin/pip install crawl4ai
venv/bin/python -m playwright install chromium 2>/dev/null || venv/bin/crawl4ai-setup || true
venv/bin/python -c "from services.research.crawl4ai_adapter import *; print('adapter imports OK')"
```

- [ ] **Step 3: Run the sidecar install + full stack**

```bash
./scripts/setup-searxng.sh
venv/bin/uvicorn app:app --port 7000 &
sleep 10
curl -s http://127.0.0.1:8893/healthz   # sidecar started by Apollo
curl -s -X POST http://127.0.0.1:7000/api/search/query -H 'Content-Type: application/json' -d '{"q":"current weather Stockholm"}' | head -c 300
```
Expected: `OK` from sidecar; search results JSON via SearXNG.

- [ ] **Step 4: Verify immediate fallback** — kill the sidecar process (`pkill -f "searx.webapp"`), repeat the search call, and confirm: results still return, response time is not 15+ seconds, and the app log shows `SearXNG sidecar not serving — skipping straight to fallback providers`.

- [ ] **Step 5: Manual checklist with a local model warm** (run in the UI):
  - Chat mode, web auto: "What is the latest Python release?" → answer cites fresh sources; log shows `auto-search`.
  - Chat mode, web auto: "Refactor: `def f(x): return x+1` to add type hints" → no search; log shows `auto-skip`.
  - Agent mode, web auto: "Find the top HN story right now and summarize it" → model calls `web_search` tool.
  - Deep Research sidebar: run a small topic; confirm search → crawl → cited report completes on the local model.

- [ ] **Step 6: Full test suite + commit any fixups**

```bash
venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
git add -A && git commit -m "chore: enable deep research, verify crawl4ai + e2e web access" || true
```

---

## Self-Review (done at plan time)

- **Spec coverage:** sidecar (Tasks 2–5, 8, 13), immediate DDG fallback incl. zero-result fall-through (existing chain behavior + Task 6 skip + timeouts), auto-search decider (Tasks 9–11), tri-state UI (Task 12), provider badge groundwork (Task 7; surfaced via existing spinner label + fallback log — full chat-bubble badge deliberately deferred as YAGNI given sources now carry `provider`), deep research/crawl4ai/agent verification (Task 14), strict local-first (no keyed providers touched anywhere).
- **Type consistency:** `resolve_web_access` returns `(use_web, allow_web_search, decision)` everywhere; `SearxngConfig` fields match between Tasks 2–3; `web_access` values `'off'|'auto'|'always'` consistent across JS and Python.
- **Known judgment calls:** `_ask_utility_model` must adapt to the real `resolve_endpoint` signature (flagged in Task 10); admin-gate idiom must copy settings routes (flagged in Task 8); JS save-pattern copied from line ~1594 (flagged in Task 13).
