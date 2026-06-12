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

    def set_catalog(self, models: list[LocalModel]) -> None:
        """Replace the in-memory catalog (lock-held) — used after a rescan."""
        with self._lock:
            self._catalog = {m.id: m for m in models}

    def stop(self, model_id: str) -> bool:
        """Stop a running model by id. Returns True if it was running.

        Lock-held so it can't race ensure_running's slot bookkeeping.
        """
        with self._lock:
            for slot in (self._chat, self._embed):
                if slot and slot.model_id == model_id:
                    self._stop_proc(slot)
                    return True
        return False

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
        if m.kind == "unsupported":
            raise ValueError(
                f"'{m.name}' (architecture: {m.arch or 'unknown'}) is not a "
                "chat-capable model — llama-server cannot serve it"
            )
        with self._lock:
            # Embedding GGUFs get an independent slot (served with --embedding)
            # so they can run alongside a chat model. Today this is reachable
            # only via an explicit start/select; RAG still defaults to
            # fastembed, so the embedding slot has no implicit caller yet.
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

    def _serving_context(self, m: LocalModel) -> int:
        """Context window to launch llama-server with.

        Apollo's prompt packer budgets against the model's KNOWN window, so a
        fixed small -c rejects long chats with HTTP 400 ("request exceeds the
        available context size"). Serve min(known window, cap) instead — the
        cap (APOLLO_LLAMA_CONTEXT, default 16384) keeps the KV cache bounded;
        the configured default stays the floor.
        """
        cap = self._context
        try:
            cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)
        except ValueError:
            cap = max(16384, self._context)
        try:
            from src.model_context import _lookup_known
            known = _lookup_known(m.name or m.id)
        except Exception:
            known = None
        if known:
            return max(self._context, min(known, cap))
        return cap

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
            "-c", str(self._serving_context(m)),
        ]
        if m.kind == "embedding":
            cmd.append("--embedding")
        log_path = os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")
        logf = open(log_path, "w")
        logger.info("Starting llama-server: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
        finally:
            # The child owns its own copy of the descriptor; keeping the
            # parent's open leaks one fd per model launch.
            logf.close()
        base_url = f"http://{self._host}:{port}"
        try:
            self._wait_health(base_url, proc, log_path,
                              timeout=self._health_timeout_for(m))
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            raise
        return _Proc(m.id, m.name, m.kind, port, proc, base_url, log_path)

    def _health_timeout_for(self, m: LocalModel) -> float:
        """Big GGUFs (external drives, MoE models) plus large -c values take
        far longer than the base timeout to load. Measured live: a 8.4GB 14B
        at -c 16384 needs >180s on this hardware. Allow ~40s/GB with the
        configured timeout as the floor."""
        size_gb = (m.size_bytes or 0) / (1024 ** 3)
        return max(self._health_timeout, size_gb * 40.0)

    def _wait_health(self, base_url: str, proc: subprocess.Popen, log_path: str,
                     timeout: Optional[float] = None) -> None:
        deadline = time.monotonic() + (timeout if timeout else self._health_timeout)
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
