# Local Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover on-disk GGUF chat/embedding models in user-configured directories, surface them in Apollo's existing model picker, and auto-launch `llama-server` when one is selected (single warm chat model).

**Architecture:** A single managed `ModelEndpoint` row (`base_url = "local://llama.cpp"`) carries the discovered models, so they flow through the existing `/api/models` picker with no new aggregation. A scanner builds the catalog; a server manager owns `llama-server` lifecycles; a one-line hook in `src/llm_core.py` materializes the `local://` sentinel into a live `http://127.0.0.1:<port>` at dispatch time (idempotent, so it survives evictions). All HTTP-probe paths skip `local://` endpoints because their model list comes from the filesystem, not a `/v1/models` probe.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, `llama-server` (llama.cpp, on PATH via Homebrew), pytest (`asyncio_mode = "auto"`).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `services/localmodels/__init__.py` | Package marker. |
| `services/localmodels/scanner.py` | Pure: scan dirs → `list[LocalModel]` (id, name, path, quant, kind, size). |
| `services/localmodels/config.py` | Resolve/persist `local_model_dirs` (settings + `APOLLO_MODELS_DIRS` env seed). |
| `services/localmodels/server_manager.py` | `llama-server` lifecycle: `ensure_running`, single-warm eviction, health wait, binary discovery. Process-wide singleton. |
| `services/localmodels/registry.py` | Bootstrap/sync the managed `ModelEndpoint` row (`local://llama.cpp`) from the catalog. |
| `routes/localmodels_routes.py` | `setup_localmodels_routes()` → API for list/scan/dirs/start/stop. |
| `src/settings.py` | Add `"local_model_dirs": []` default. |
| `src/llm_core.py` | Add `local://` materialization at top of `llm_call_async` + `stream_llm`. |
| `routes/model_routes.py` | `_classify_endpoint` → "local" for `local://`; skip `local://` in probe paths. |
| `app.py` | Register router; kick off non-blocking startup scan. |
| `.env.example` | Document `APOLLO_MODELS_DIRS`. |
| `tests/test_localmodels_*.py` | Unit tests. |

---

## Task 1: Settings key + directory config

**Files:**
- Modify: `src/settings.py` (DEFAULT_SETTINGS dict, near line 113)
- Create: `services/localmodels/__init__.py`
- Create: `services/localmodels/config.py`
- Create: `tests/test_localmodels_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add the settings default.** In `src/settings.py`, inside `DEFAULT_SETTINGS`, add after the `"task_model": ""` line (~line 114):

```python
    # Local model directories scanned for on-disk GGUF chat/embedding models.
    # Empty = fall back to APOLLO_MODELS_DIRS env, then built-in defaults.
    "local_model_dirs": [],
```

- [ ] **Step 2: Create the package marker.** `services/localmodels/__init__.py`:

```python
"""Local on-disk GGUF model discovery and llama-server lifecycle."""
```

- [ ] **Step 3: Write the failing test.** `tests/test_localmodels_config.py`:

```python
import os
import importlib
from unittest.mock import patch


def test_env_seed_parses_comma_and_pathsep(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_MODELS_DIRS", "/a/models,/b/models")
    with patch.object(config, "load_settings", return_value={"local_model_dirs": []}):
        dirs = config.get_local_model_dirs()
    assert dirs == ["/a/models", "/b/models"]


def test_settings_override_env(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_MODELS_DIRS", "/ignored")
    with patch.object(config, "load_settings", return_value={"local_model_dirs": ["/chosen"]}):
        dirs = config.get_local_model_dirs()
    assert dirs == ["/chosen"]


def test_default_when_unset(monkeypatch):
    from services.localmodels import config
    monkeypatch.delenv("APOLLO_MODELS_DIRS", raising=False)
    with patch.object(config, "load_settings", return_value={"local_model_dirs": []}):
        dirs = config.get_local_model_dirs()
    assert dirs == config.DEFAULT_DIRS
```

- [ ] **Step 4: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_config.py -v`. Expected: FAIL (`ModuleNotFoundError: services.localmodels.config`).

- [ ] **Step 5: Implement `services/localmodels/config.py`:**

```python
"""Resolve and persist the directories scanned for local GGUF models."""
from __future__ import annotations

import os

from src.settings import load_settings, save_settings

ENV_VAR = "APOLLO_MODELS_DIRS"
DEFAULT_DIRS = [
    "/Volumes/MainStore/Development/AI_Models",
    os.path.expanduser("~/Desktop/AI_Models"),
]


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
    """Persist the directory list and return the cleaned value."""
    cleaned = [d.strip() for d in (dirs or []) if d and d.strip()]
    settings = load_settings()
    settings["local_model_dirs"] = cleaned
    save_settings(settings)
    # Invalidate the settings cache so the next read sees the new value.
    import src.settings as _s
    _s._settings_cache = None
    return cleaned
```

- [ ] **Step 6: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_config.py -v`. Expected: 3 passed.

- [ ] **Step 7: Document the env var.** In `.env.example`, add near the LLM host settings:

```bash
# Directories scanned for on-disk GGUF chat/embedding models (comma- or
# os.pathsep-separated). Overridden by the "local_model_dirs" setting if set.
# APOLLO_MODELS_DIRS=/Volumes/MainStore/Development/AI_Models,~/Desktop/AI_Models
```

- [ ] **Step 8: Commit.**

```bash
git add src/settings.py services/localmodels/__init__.py services/localmodels/config.py tests/test_localmodels_config.py .env.example
git commit -m "feat(local-models): settings key + directory config"
```

---

## Task 2: GGUF scanner

**Files:**
- Create: `services/localmodels/scanner.py`
- Create: `tests/test_localmodels_scanner.py`

- [ ] **Step 1: Write the failing test.** `tests/test_localmodels_scanner.py`:

```python
import os


def _touch(path, size=16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def test_scan_classifies_chat_and_embedding(tmp_path):
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _touch(str(d / "Qwen3.5-9B-Q4_K_M.gguf"))
    _touch(str(d / "nomic-embed-text-v1.5.f16.gguf"))
    models = scan_dirs([str(tmp_path)])
    by_name = {m.name: m for m in models}
    assert by_name["Qwen3.5-9B-Q4_K_M"].kind == "chat"
    assert by_name["Qwen3.5-9B-Q4_K_M"].quant == "Q4_K_M"
    assert by_name["nomic-embed-text-v1.5.f16"].kind == "embedding"


def test_scan_skips_sidecars_projectors_and_extra_split_parts(tmp_path):
    from services.localmodels.scanner import scan_dirs
    d = tmp_path / "GGUF"
    _touch(str(d / "._Qwen3.5-9B-Q4_K_M.gguf"))      # AppleDouble sidecar
    _touch(str(d / "mmproj-model-f16.gguf"))          # multimodal projector
    _touch(str(d / "Big-Model-2-of-3.gguf"))          # non-first split part
    _touch(str(d / "Big-Model-1-of-3.gguf"))          # first split part (kept)
    names = {m.name for m in scan_dirs([str(tmp_path)])}
    assert names == {"Big-Model-1-of-3"}


def test_scan_tolerates_missing_dir():
    from services.localmodels.scanner import scan_dirs
    assert scan_dirs(["/nonexistent/path/xyz"]) == []
```

- [ ] **Step 2: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_scanner.py -v`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `services/localmodels/scanner.py`:**

```python
"""Discover local GGUF chat/embedding models under configured directories."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

# Quant regex ported from routes/cookbook_helpers.py (_cached_model_scan_script).
_QUANT_RE = re.compile(
    r"(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)"
)
_EMBED_HINT = re.compile(r"(?i)(embed|nomic|bge|gte|e5|minilm)")
_SPLIT_RE = re.compile(r"(?i)^(.+)-(\d+)-of-(\d+)\.gguf$")


@dataclass
class LocalModel:
    id: str
    name: str
    path: str
    quant: str
    kind: str  # "chat" | "embedding"
    size_bytes: int
    directory: str


def _quant(name: str) -> str:
    m = _QUANT_RE.search(name)
    return m.group(0).upper() if m else ""


def _is_projector(name: str) -> bool:
    n = name.lower()
    return n.startswith("mmproj") or "mmproj" in n


def _kind(name: str) -> str:
    return "embedding" if _EMBED_HINT.search(name) else "chat"


def _model_id(path: str) -> str:
    return "lm_" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def scan_dirs(dirs: list[str]) -> list[LocalModel]:
    """Walk each directory and return discovered GGUF models (deduped by path)."""
    out: dict[str, LocalModel] = {}
    for raw in dirs or []:
        base = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isdir(base):
            continue
        for root, subdirs, files in os.walk(base, followlinks=False):
            for fn in sorted(files):
                if not fn.lower().endswith(".gguf"):
                    continue
                if fn.startswith("._"):
                    continue
                if _is_projector(fn):
                    continue
                split = _SPLIT_RE.match(fn)
                if split and split.group(2) != str(int(split.group(2))):
                    continue
                if split and int(split.group(2)) != 1:
                    continue  # only register the first part of a split model
                fp = os.path.join(root, fn)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                mid = _model_id(fp)
                if mid in out:
                    continue
                out[mid] = LocalModel(
                    id=mid,
                    name=fn[:-5],  # strip ".gguf"
                    path=fp,
                    quant=_quant(fn),
                    kind=_kind(fn),
                    size_bytes=size,
                    directory=base,
                )
    return list(out.values())
```

- [ ] **Step 4: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_scanner.py -v`. Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add services/localmodels/scanner.py tests/test_localmodels_scanner.py
git commit -m "feat(local-models): GGUF directory scanner"
```

---

## Task 3: llama-server lifecycle manager

**Files:**
- Create: `services/localmodels/server_manager.py`
- Create: `tests/test_localmodels_server.py`

- [ ] **Step 1: Write the failing test** (stubs the launch so no real process runs). `tests/test_localmodels_server.py`:

```python
from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer, _Proc


def _model(mid, name, kind="chat"):
    return LocalModel(id=mid, name=name, path=f"/m/{name}.gguf",
                      quant="Q4_K_M", kind=kind, size_bytes=1, directory="/m")


class _FakeProcess:
    def __init__(self):
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False


def _server_with(models, launched):
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv._catalog = {m.id: m for m in models}

    def fake_launch(m):
        p = _FakeProcess()
        proc = _Proc(model_id=m.id, name=m.name, kind=m.kind,
                     port=9000 + len(launched), proc=p,
                     base_url=f"http://127.0.0.1:{9000 + len(launched)}")
        launched.append(proc)
        return proc

    srv._launch = fake_launch  # type: ignore[assignment]
    return srv


def test_ensure_running_starts_and_reuses_when_warm():
    launched = []
    srv = _server_with([_model("a", "ModelA")], launched)
    url1 = srv.ensure_running("ModelA")
    url2 = srv.ensure_running("ModelA")  # already warm → no new launch
    assert url1 == url2
    assert len(launched) == 1


def test_new_chat_model_evicts_previous():
    launched = []
    srv = _server_with([_model("a", "ModelA"), _model("b", "ModelB")], launched)
    srv.ensure_running("ModelA")
    srv.ensure_running("ModelB")
    assert launched[0].proc.terminated is True   # A was stopped
    assert launched[1].proc.terminated is False  # B is warm


def test_embedding_does_not_evict_chat():
    launched = []
    srv = _server_with(
        [_model("a", "ModelA", "chat"), _model("e", "Embed", "embedding")], launched
    )
    srv.ensure_running("ModelA")
    srv.ensure_running("Embed")
    assert launched[0].proc.terminated is False  # chat stays warm
    assert len(launched) == 2


def test_unknown_model_raises():
    srv = _server_with([_model("a", "ModelA")], [])
    srv._dirs_provider = lambda: []  # refresh finds nothing
    try:
        srv.ensure_running("Nope")
        assert False, "expected LookupError"
    except LookupError:
        pass
```

- [ ] **Step 2: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_server.py -v`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `services/localmodels/server_manager.py`:**

```python
"""Launch and track local llama-server processes (single warm chat model)."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from services.localmodels.scanner import LocalModel, scan_dirs

logger = logging.getLogger(__name__)

_BIN_CANDIDATES = [
    "llama-server",
    os.path.expanduser("~/.local/bin/llama-server"),
    os.path.expanduser("~/bin/llama-server"),
    os.path.expanduser("~/llama.cpp/build/bin/llama-server"),
    "/opt/homebrew/bin/llama-server",
    "/usr/local/bin/llama-server",
]


@dataclass
class _Proc:
    model_id: str
    name: str
    kind: str
    port: int
    proc: subprocess.Popen
    base_url: str
    log_path: str = ""


def _free_port(host: str) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


class LocalModelServer:
    def __init__(
        self,
        dirs_provider: Callable[[], list[str]],
        host: str = "127.0.0.1",
        health_timeout: float = 180.0,
        context: int = 4096,
    ):
        self._dirs_provider = dirs_provider
        self._host = host
        self._health_timeout = health_timeout
        self._context = context
        self._lock = threading.RLock()
        self._chat: Optional[_Proc] = None
        self._embed: Optional[_Proc] = None
        self._catalog: dict[str, LocalModel] = {}

    # -- discovery --------------------------------------------------------
    def find_binary(self) -> Optional[str]:
        for cand in _BIN_CANDIDATES:
            if os.sep in cand:
                if os.path.exists(cand) and os.access(cand, os.X_OK):
                    return cand
            else:
                found = shutil.which(cand)
                if found:
                    return found
        return None

    def refresh_catalog(self) -> list[LocalModel]:
        models = scan_dirs(self._dirs_provider())
        with self._lock:
            self._catalog = {m.id: m for m in models}
        return models

    def catalog(self) -> list[LocalModel]:
        with self._lock:
            return list(self._catalog.values())

    def _resolve(self, ref: str) -> Optional[LocalModel]:
        with self._lock:
            if ref in self._catalog:
                return self._catalog[ref]
            for m in self._catalog.values():
                if m.name == ref:
                    return m
        return None

    # -- lifecycle --------------------------------------------------------
    def ensure_running(self, ref: str) -> str:
        m = self._resolve(ref)
        if m is None:
            self.refresh_catalog()
            m = self._resolve(ref)
        if m is None:
            raise LookupError(f"Unknown local model: {ref!r}")
        with self._lock:
            slot = self._embed if m.kind == "embedding" else self._chat
            if slot and slot.model_id == m.id and slot.proc.poll() is None:
                return slot.base_url
            if slot:
                self._stop_proc(slot)
            proc = self._launch(m)
            if m.kind == "embedding":
                self._embed = proc
            else:
                self._chat = proc
            return proc.base_url

    def _launch(self, m: LocalModel) -> _Proc:
        binary = self.find_binary()
        if not binary:
            raise RuntimeError(
                "llama-server not found. Install llama.cpp "
                "(e.g. `brew install llama.cpp`) or build it via the Cookbook."
            )
        port = _free_port(self._host)
        cmd = [
            binary, "--model", m.path,
            "--host", self._host, "--port", str(port),
            "-c", str(self._context),
        ]
        if m.kind == "embedding":
            cmd.append("--embedding")
        log_path = os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")
        logf = open(log_path, "w")
        logger.info("Starting llama-server: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        base_url = f"http://{self._host}:{port}"
        try:
            self._wait_health(base_url, proc, log_path)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            raise
        return _Proc(m.id, m.name, m.kind, port, proc, base_url, log_path)

    def _wait_health(self, base_url: str, proc: subprocess.Popen, log_path: str) -> None:
        deadline = time.monotonic() + self._health_timeout
        url = base_url + "/health"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited early (code {proc.returncode}):\n"
                    f"{_tail(log_path)}"
                )
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise TimeoutError("llama-server did not become healthy in time")

    def _stop_proc(self, slot: _Proc) -> None:
        try:
            slot.proc.terminate()
            slot.proc.wait(timeout=10)
        except Exception:
            try:
                slot.proc.kill()
            except Exception:
                pass
        if slot is self._chat:
            self._chat = None
        if slot is self._embed:
            self._embed = None

    def stop_all(self) -> None:
        with self._lock:
            for slot in (self._chat, self._embed):
                if slot:
                    self._stop_proc(slot)

    def status(self) -> dict:
        with self._lock:
            out = {}
            for slot in (self._chat, self._embed):
                if slot:
                    out[slot.model_id] = {
                        "name": slot.name, "kind": slot.kind, "port": slot.port,
                        "running": slot.proc.poll() is None, "base_url": slot.base_url,
                    }
            return out


def _tail(path: str, n: int = 2000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-n:]
    except OSError:
        return ""


# -- process-wide singleton ----------------------------------------------
_SERVER: Optional[LocalModelServer] = None
_SERVER_LOCK = threading.Lock()


def get_server() -> LocalModelServer:
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER is None:
            from services.localmodels.config import get_local_model_dirs
            _SERVER = LocalModelServer(dirs_provider=get_local_model_dirs)
        return _SERVER
```

- [ ] **Step 4: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_server.py -v`. Expected: 4 passed.

- [ ] **Step 5: Commit.**

```bash
git add services/localmodels/server_manager.py tests/test_localmodels_server.py
git commit -m "feat(local-models): llama-server lifecycle manager"
```

---

## Task 4: Managed endpoint registry + picker/probe integration

**Files:**
- Create: `services/localmodels/registry.py`
- Modify: `routes/model_routes.py` (`_classify_endpoint` + the three probe paths)
- Create: `tests/test_localmodels_registry.py`

- [ ] **Step 1: Write the failing test** (uses a fake DB session; no real DB). `tests/test_localmodels_registry.py`:

```python
from services.localmodels.scanner import LocalModel
from services.localmodels import registry


class _FakeEP:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.committed = False

    def query(self, *a):
        return _FakeQuery(self.rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _models():
    return [
        LocalModel("a", "Qwen3.5-9B-Q4_K_M", "/m/a.gguf", "Q4_K_M", "chat", 1, "/m"),
        LocalModel("e", "nomic-embed", "/m/e.gguf", "F16", "embedding", 1, "/m"),
    ]


def test_creates_managed_endpoint_when_absent(monkeypatch):
    sess = _FakeSession(rows=[])
    monkeypatch.setattr(registry, "SessionLocal", lambda: sess)
    monkeypatch.setattr(registry, "ModelEndpoint", _FakeEP)
    registry.sync_managed_endpoint(_models())
    assert len(sess.added) == 1
    ep = sess.added[0]
    assert ep.base_url == registry.LOCAL_BASE_URL
    assert "Qwen3.5-9B-Q4_K_M" in ep.cached_models
    assert sess.committed is True


def test_updates_existing_endpoint(monkeypatch):
    existing = _FakeEP(base_url=registry.LOCAL_BASE_URL, cached_models="[]",
                       is_enabled=False)
    sess = _FakeSession(rows=[existing])
    monkeypatch.setattr(registry, "SessionLocal", lambda: sess)
    monkeypatch.setattr(registry, "ModelEndpoint", _FakeEP)
    registry.sync_managed_endpoint(_models())
    assert sess.added == []
    assert existing.is_enabled is True
    assert "nomic-embed" in existing.cached_models
```

- [ ] **Step 2: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_registry.py -v`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `services/localmodels/registry.py`:**

```python
"""Maintain the single managed ModelEndpoint that represents local models."""
from __future__ import annotations

import json
import logging
import uuid

from core.database import SessionLocal, ModelEndpoint
from services.localmodels.scanner import LocalModel

logger = logging.getLogger(__name__)

LOCAL_BASE_URL = "local://llama.cpp"
LOCAL_ENDPOINT_NAME = "Local (llama.cpp)"


def is_local_endpoint(base_url: str | None) -> bool:
    return bool(base_url) and base_url.startswith("local://")


def sync_managed_endpoint(models: list[LocalModel]) -> None:
    """Create or update the managed endpoint's cached_models from the catalog.

    Chat models are listed first, then embedding models. Safe to call repeatedly.
    """
    names = [m.name for m in models if m.kind == "chat"]
    names += [m.name for m in models if m.kind == "embedding"]
    payload = json.dumps(names)
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.base_url == LOCAL_BASE_URL
        ).first()
        if ep is None:
            ep = ModelEndpoint(
                id=str(uuid.uuid4()),
                name=LOCAL_ENDPOINT_NAME,
                base_url=LOCAL_BASE_URL,
                api_key=None,
                is_enabled=True,
                model_type="llm",
                owner=None,
                cached_models=payload,
            )
            db.add(ep)
        else:
            ep.cached_models = payload
            ep.is_enabled = True
        db.commit()
        logger.info("Synced %d local models into managed endpoint", len(names))
    except Exception as e:  # never let a scan crash the caller
        logger.warning("Failed to sync managed local endpoint: %s", e)
    finally:
        db.close()
```

- [ ] **Step 4: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_registry.py -v`. Expected: 2 passed.

- [ ] **Step 5: Classify `local://` as a local group.** In `routes/model_routes.py`, at the top of `_classify_endpoint` (just inside the function, before the `try`):

```python
def _classify_endpoint(base_url: str) -> str:
    if (base_url or "").startswith("local://"):
        return "local"
    # ...existing body unchanged...
```

- [ ] **Step 6: Skip `local://` in every probe path.** In `routes/model_routes.py`, guard the three places that HTTP-probe endpoints so they never overwrite the managed endpoint's `cached_models`:
  - In `_refresh_caches_bg` (~line 667-722): when iterating endpoints, `continue` if `(ep.base_url or "").startswith("local://")`.
  - In the `/api/probe` SSE handler (~line 1014-1077): same `continue` guard in its endpoint loop.
  - In `/api/model-endpoints/{ep_id}/probe` (~line 1299-1330): early-return / skip if the endpoint's `base_url` starts with `local://`.

Add this helper near the top of `routes/model_routes.py` and use it in each loop:

```python
def _is_local_managed(base_url) -> bool:
    return bool(base_url) and str(base_url).startswith("local://")
```

Apply at each probe site, e.g. inside the endpoint loop:

```python
        if _is_local_managed(ep.base_url):
            continue  # local models are filesystem-scanned, not HTTP-probed
```

For the single-endpoint probe route, after loading the endpoint row:

```python
    if _is_local_managed(ep.base_url):
        return JSONResponse({"models": json.loads(ep.cached_models or "[]")})
```

- [ ] **Step 7: Run the existing model-routes tests to confirm no regression.** Run: `python3 -m pytest tests/test_model_routes.py -v`. Expected: all still pass.

- [ ] **Step 8: Commit.**

```bash
git add services/localmodels/registry.py routes/model_routes.py tests/test_localmodels_registry.py
git commit -m "feat(local-models): managed endpoint + picker/probe integration"
```

---

## Task 5: Auto-serve hook in llm_core

**Files:**
- Modify: `src/llm_core.py` (`llm_call_async`, `stream_llm`)
- Create: `tests/test_localmodels_hook.py`

- [ ] **Step 1: Write the failing test.** `tests/test_localmodels_hook.py`:

```python
from src.llm_core import materialize_local_url


def test_passthrough_for_normal_url():
    assert materialize_local_url("https://api.openai.com/v1/chat/completions",
                                 "gpt-4o") == \
        "https://api.openai.com/v1/chat/completions"


def test_local_sentinel_materializes(monkeypatch):
    class _FakeServer:
        def ensure_running(self, ref):
            assert ref == "Qwen3.5-9B-Q4_K_M"
            return "http://127.0.0.1:9999"

    import services.localmodels.server_manager as sm
    monkeypatch.setattr(sm, "get_server", lambda: _FakeServer())
    url = materialize_local_url("local://llama.cpp/chat/completions",
                                "Qwen3.5-9B-Q4_K_M")
    assert url == "http://127.0.0.1:9999/v1/chat/completions"
```

- [ ] **Step 2: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_hook.py -v`. Expected: FAIL (`ImportError: cannot import name 'materialize_local_url'`).

- [ ] **Step 3: Implement the helper** in `src/llm_core.py` (near the top-level helpers, e.g. just above `def _detect_provider`):

```python
def materialize_local_url(url: str, model: str) -> str:
    """Turn a `local://llama.cpp...` sentinel into a live llama-server URL.

    Idempotent: starts the model's server if needed (evicting the previous
    warm chat model), then returns its OpenAI-compatible chat endpoint. Normal
    URLs pass through unchanged.
    """
    if not isinstance(url, str) or not url.startswith("local://"):
        return url
    from services.localmodels.server_manager import get_server
    base = get_server().ensure_running(model)
    return base.rstrip("/") + "/v1/chat/completions"
```

- [ ] **Step 4: Call the helper at the start of both dispatch functions.** In `src/llm_core.py`, as the FIRST statement inside `llm_call_async(url, model, messages, ...)` (before provider detection ~line 945) add:

```python
    url = materialize_local_url(url, model)
```

And as the FIRST statement inside `stream_llm(url, model, messages, ...)` (before its provider detection ~line 1049) add the same line:

```python
    url = materialize_local_url(url, model)
```

- [ ] **Step 5: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_hook.py -v`. Expected: 2 passed.

- [ ] **Step 6: Commit.**

```bash
git add src/llm_core.py tests/test_localmodels_hook.py
git commit -m "feat(local-models): auto-serve hook materializes local:// at dispatch"
```

---

## Task 6: API routes + startup wiring

**Files:**
- Create: `routes/localmodels_routes.py`
- Modify: `app.py` (import, `include_router`, startup scan thread)
- Create: `tests/test_localmodels_routes.py`

- [ ] **Step 1: Write the failing test** (calls the pure scan/sync orchestrator, not the HTTP layer). `tests/test_localmodels_routes.py`:

```python
from services.localmodels import lifecycle


def test_rescan_returns_catalog_and_syncs(monkeypatch):
    from services.localmodels.scanner import LocalModel
    fake = [LocalModel("a", "ModelA", "/m/a.gguf", "Q4_K_M", "chat", 1, "/m")]
    monkeypatch.setattr(lifecycle, "scan_dirs", lambda dirs: fake)
    monkeypatch.setattr(lifecycle, "get_local_model_dirs", lambda: ["/m"])
    synced = {}
    monkeypatch.setattr(lifecycle, "sync_managed_endpoint",
                        lambda models: synced.setdefault("n", len(models)))
    result = lifecycle.rescan()
    assert [m.name for m in result] == ["ModelA"]
    assert synced["n"] == 1
```

- [ ] **Step 2: Run it — expect failure.** Run: `python3 -m pytest tests/test_localmodels_routes.py -v`. Expected: FAIL (`ModuleNotFoundError: services.localmodels.lifecycle`).

- [ ] **Step 3: Implement the orchestrator** `services/localmodels/lifecycle.py`:

```python
"""Scan + sync orchestration shared by the API routes and startup."""
from __future__ import annotations

import logging

from services.localmodels.config import get_local_model_dirs
from services.localmodels.scanner import scan_dirs, LocalModel
from services.localmodels.registry import sync_managed_endpoint
from services.localmodels.server_manager import get_server

logger = logging.getLogger(__name__)


def rescan() -> list[LocalModel]:
    """Scan configured dirs, refresh the server catalog, and sync the picker."""
    models = scan_dirs(get_local_model_dirs())
    get_server()._catalog = {m.id: m for m in models}  # keep server in sync
    sync_managed_endpoint(models)
    return models


def startup_scan() -> None:
    try:
        rescan()
    except Exception as e:
        logger.warning("Local model startup scan failed: %s", e)
```

- [ ] **Step 4: Run tests — expect pass.** Run: `python3 -m pytest tests/test_localmodels_routes.py -v`. Expected: 1 passed.

- [ ] **Step 5: Implement `routes/localmodels_routes.py`** (mirrors the `setup_*_routes()` pattern used across `routes/`):

```python
"""HTTP API for local on-disk GGUF models."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.localmodels import lifecycle
from services.localmodels.config import get_local_model_dirs, set_local_model_dirs
from services.localmodels.server_manager import get_server


class DirsBody(BaseModel):
    dirs: list[str]


def setup_localmodels_routes() -> APIRouter:
    router = APIRouter(prefix="/api/local-models", tags=["local-models"])

    @router.get("")
    def list_models():
        server = get_server()
        status = server.status()
        catalog = lifecycle.scan_dirs(get_local_model_dirs())
        running_ids = set(status.keys())
        return {
            "dirs": get_local_model_dirs(),
            "models": [
                {**asdict(m), "running": m.id in running_ids}
                for m in catalog
            ],
        }

    @router.post("/scan")
    def rescan():
        models = lifecycle.rescan()
        return {"count": len(models), "models": [asdict(m) for m in models]}

    @router.get("/dirs")
    def get_dirs():
        return {"dirs": get_local_model_dirs()}

    @router.put("/dirs")
    def put_dirs(body: DirsBody):
        dirs = set_local_model_dirs(body.dirs)
        lifecycle.rescan()
        return {"dirs": dirs}

    @router.post("/{model_id}/start")
    def start(model_id: str):
        try:
            url = get_server().ensure_running(model_id)
            return {"ok": True, "base_url": url}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @router.post("/{model_id}/stop")
    def stop(model_id: str):
        server = get_server()
        for slot in (server._chat, server._embed):
            if slot and slot.model_id == model_id:
                server._stop_proc(slot)
                return {"ok": True}
        return {"ok": False, "error": "not running"}

    return router
```

Note: add `from services.localmodels.scanner import scan_dirs` to `lifecycle.py`'s exports by importing it there (already imported in Step 3) and reference `lifecycle.scan_dirs` from the route; if cleaner, import `scan_dirs` directly in the route module instead.

- [ ] **Step 6: Wire into `app.py`.** Add the import alongside the other route imports, register the router near the other `include_router` calls (after `setup_hwfit_routes()` at ~line 638):

```python
from routes.localmodels_routes import setup_localmodels_routes
# ...
app.include_router(setup_localmodels_routes())
```

Then add a non-blocking startup scan. After the router registrations (near the end of the `include_router` block), add:

```python
import threading
from services.localmodels.lifecycle import startup_scan
threading.Thread(target=startup_scan, name="local-models-scan", daemon=True).start()
```

- [ ] **Step 7: Smoke-test import + route registration.** Run:

```bash
python3 -c "import app; print([r.path for r in app.app.routes if 'local-models' in r.path])"
```

Expected: prints the 6 `/api/local-models...` paths without error.

- [ ] **Step 8: Commit.**

```bash
git add routes/localmodels_routes.py services/localmodels/lifecycle.py app.py tests/test_localmodels_routes.py
git commit -m "feat(local-models): API routes + non-blocking startup scan"
```

---

## Task 7: Settings → Local Models panel (frontend)

**Files:**
- Modify: the settings page JS/HTML (identify exact file in Step 1)
- Modify: `static/index.html` if a nav entry is needed

The picker already shows local models for free (Task 4), so this panel is for **managing directories and serve state**, not for selection.

- [ ] **Step 1: Locate the settings UI pattern.** Run:

```bash
ls static/js | grep -i -E 'setting|prefs|admin'
grep -rn "model-endpoints" static/js | head
```

Read the file that renders the existing "Model Endpoints" / providers settings section. Mirror its DOM-creation and `fetch` patterns exactly.

- [ ] **Step 2: Add a "Local Models" section** to that settings view that:
  - `GET /api/local-models` on open → render a directory list (each removable) + an "Add directory" input, and a model table (name, quant, kind, size, Running badge, Start/Stop button).
  - "Add"/"Remove" directory → `PUT /api/local-models/dirs` with the full `{dirs: [...]}` list, then re-render.
  - "Rescan" button → `POST /api/local-models/scan`, then re-render.
  - Start button → `POST /api/local-models/{id}/start`; Stop button → `POST /api/local-models/{id}/stop`; show a "Loading…" state on the row while the request is in flight (server start can take seconds).

API contract (already implemented in Task 6):
  - `GET /api/local-models` → `{ dirs: string[], models: [{id,name,path,quant,kind,size_bytes,directory,running}] }`
  - `PUT /api/local-models/dirs` body `{dirs: string[]}` → `{dirs: string[]}`
  - `POST /api/local-models/scan` → `{count, models}`
  - `POST /api/local-models/{id}/start` → `{ok, base_url}` or `{ok:false,error}` (status 400)
  - `POST /api/local-models/{id}/stop` → `{ok}`

- [ ] **Step 3: Manually verify in the browser.** Start the app, open Settings → Local Models, confirm the 20 chat models + embedding model from `/Volumes/MainStore/Development/AI_Models` appear, add/remove a directory, and Start a small model.

- [ ] **Step 4: Commit.**

```bash
git add static/
git commit -m "feat(local-models): settings panel to manage dirs and serve state"
```

---

## Task 8: End-to-end verification

- [ ] **Step 1: Run the full local-models unit suite.** Run:

```bash
python3 -m pytest tests/test_localmodels_*.py -v
```

Expected: all pass.

- [ ] **Step 2: Optional gated integration test** (real serve of the smallest model). Create `tests/test_localmodels_integration.py`:

```python
import os
import urllib.request
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("APOLLO_LOCAL_MODEL_IT") != "1",
    reason="set APOLLO_LOCAL_MODEL_IT=1 to run the real llama-server integration test",
)


def test_serve_smallest_model():
    from services.localmodels.server_manager import LocalModelServer
    srv = LocalModelServer(
        dirs_provider=lambda: ["/Volumes/MainStore/Development/AI_Models"]
    )
    srv.refresh_catalog()
    base = srv.ensure_running("Llama-3.2-1B-Instruct-Q4_K_M")
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            assert r.status == 200
    finally:
        srv.stop_all()
```

Run (only when you want the real test): `APOLLO_LOCAL_MODEL_IT=1 python3 -m pytest tests/test_localmodels_integration.py -v`.

- [ ] **Step 3: Manual smoke in the app.** Launch Apollo, open the chat model picker, confirm a "Local (llama.cpp)" group with your GGUF models, select `Llama-3.2-1B-Instruct-Q4_K_M`, send a message, and confirm a reply (first message warms the server, then it's fast). Switch to another local model and confirm the previous one is evicted (check `status()` / process list).

- [ ] **Step 4: Final commit / open PR.**

```bash
git add -A
git commit -m "test(local-models): gated end-to-end integration test"
```

---

## Self-Review Notes (coverage map)

- Spec "discover GGUF chat+embedding in configurable dirs" → Tasks 1, 2.
- Spec "appear in picker" → Task 4 (managed endpoint + classify), zero new picker plumbing.
- Spec "auto-serve on select, single warm chat model" → Tasks 3, 5.
- Spec "embedding served independently with --embedding" → Task 3 (`_embed` slot, `--embedding`).
- Spec "configurable dirs, Settings UI + env var" → Tasks 1, 6, 7.
- Spec error handling (missing binary, OOM, port conflict, unmounted dir, health timeout) → Task 3 (`find_binary` error, `_tail` on early exit, `_free_port`, scanner `isdir` guard, `_wait_health` timeout).
- Spec testing → Tasks 1-6 unit tests + Task 8 gated integration.
- **Probe gotcha** (probing `local://` would wipe cached_models) → Task 4 Step 6 guards all three probe paths.
