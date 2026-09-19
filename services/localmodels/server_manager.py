"""Launch and track local llama-server processes (single warm chat model)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from services.localmodels.scanner import LocalModel, scan_dirs
from services.localmodels.config import get_arch_llama_server_path, get_llama_server_path
from services.localmodels.mlx import find_mlx_runtime
from src.observability import report_exception

logger = logging.getLogger(__name__)

def _bin_candidates() -> list[str]:
    if os.name == "nt":
        local_appdata = os.environ.get(
            "LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local")
        )
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        home = os.path.expanduser("~")
        return [
            "llama-server",  # PATH lookup; PATHEXT resolves llama-server.exe
            os.path.join(home, "scoop", "shims", "llama-server.exe"),
            os.path.join(local_appdata, "llama.cpp", "llama-server.exe"),
            os.path.join(program_files, "llama.cpp", "llama-server.exe"),
            os.path.join(home, "llama.cpp", "build", "bin", "Release", "llama-server.exe"),
        ]
    return [
        "llama-server",
        os.path.expanduser("~/.local/bin/llama-server"),
        os.path.expanduser("~/bin/llama-server"),
        os.path.expanduser("~/llama.cpp/build/bin/llama-server"),
        "/opt/homebrew/bin/llama-server",
        "/usr/local/bin/llama-server",
    ]


_BIN_CANDIDATES = _bin_candidates()
# Output cap mlx_lm.server applies when a request sets none (its own default
# is 512). Generation still stops at end-of-turn; this only bounds runaways.
MLX_DEFAULT_MAX_TOKENS = 32768
_FAILED_LAUNCH_TTL = 30.0  # seconds a failed launch is replayed, not retried


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
        # model path -> whether its chat template handles OpenAI tool calls,
        # as reported by llama-server's /props after a launch.
        self._tool_caps: dict[str, bool] = {}
        # model id -> (monotonic time, error) of the last failed launch, so a
        # caller retrying right away (agent loop, then the LLM call) fails
        # fast instead of waiting out a multi-minute load a second time.
        self._failed: dict[str, tuple[float, Exception]] = {}

    # -- discovery --------------------------------------------------------
    def find_binary(self, arch: str = "") -> Optional[str]:
        # Architectures stock llama.cpp can't load run on a configured fork
        # build (e.g. k2-horizon); fall through to the default when unset.
        fork = get_arch_llama_server_path(arch)
        if fork and os.path.isfile(fork):
            return fork
        # An explicitly configured path (Settings → AI or APOLLO_LLAMA_SERVER)
        # wins outright — and if it's set but wrong we return None rather than
        # silently auto-detecting a different binary than the one asked for.
        configured = get_llama_server_path()
        if configured:
            return configured if os.path.isfile(configured) else None
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
                if (m.kind != "embedding" and m.backend != "mlx"
                        and m.path not in self._tool_caps):
                    caps = _probe_tool_calls(slot.base_url)
                    if caps is not None:
                        self._tool_caps[m.path] = caps
                return slot.base_url
            failed = self._failed.get(m.id)
            if failed and time.monotonic() - failed[0] < _FAILED_LAUNCH_TTL:
                raise failed[1]
            if slot:
                self._stop_proc(slot)
            try:
                proc = self._launch(m)
            except Exception as error:
                self._failed[m.id] = (time.monotonic(), error)
                raise
            self._failed.pop(m.id, None)
            if m.kind == "embedding":
                self._embed = proc
            else:
                self._chat = proc
                caps = m.tools if m.backend == "mlx" else _probe_tool_calls(proc.base_url)
                if caps is not None:
                    self._tool_caps[m.path] = caps
            return proc.base_url

    def supports_tool_calls(self, ref: str) -> Optional[bool]:
        """Whether a launched model's template emits native tool calls.

        None until the model has been launched once in this process.
        """
        m = self._resolve(ref)
        if m is None:
            return None
        with self._lock:
            return self._tool_caps.get(m.path)

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
        except Exception as error:
            report_exception(
                logger,
                "local_model_context_lookup_failed",
                error,
                outcome="best_effort",
                context={"model_id": m.id},
            )
            known = None
        if known:
            return max(self._context, min(known, cap))
        return cap

    def _launch(self, m: LocalModel) -> _Proc:
        port = _free_port(self._host)
        if m.backend == "mlx":
            cmd, cwd = self._mlx_command(m, port)
        else:
            cmd, cwd = self._llama_command(m, port), None
        log_path = os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")
        logf = open(log_path, "w")
        logger.info("Starting local model server: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                    text=True, cwd=cwd)
        finally:
            # The child owns its own copy of the descriptor; keeping the
            # parent's open leaks one fd per model launch.
            logf.close()
        base_url = f"http://{self._host}:{port}"
        try:
            timeout = self._health_timeout_for(m)
            self._wait_health(base_url, proc, log_path, timeout=timeout)
            if m.backend == "mlx":
                _wait_mlx_loaded(base_url, m.name, log_path, timeout)
        except Exception as error:
            report_exception(
                logger,
                "local_model_health_wait_failed",
                error,
                outcome="critical",
                context={"model_id": m.id},
            )
            try:
                proc.terminate()
            except Exception as cleanup_error:
                report_exception(
                    logger,
                    "local_model_startup_cleanup_failed",
                    cleanup_error,
                    outcome="best_effort",
                    context={"model_id": m.id},
                )
            raise
        return _Proc(m.id, m.name, m.kind, port, proc, base_url, log_path)

    def _mlx_command(self, m: LocalModel, port: int) -> tuple[list[str], str]:
        """mlx_lm.server command plus the working directory to run it in.

        mlx_lm.server loads whatever the request's `model` field names, and
        Apollo sends the catalog name. Serving from a directory where that
        name is a symlink to the model folder makes both resolve to the same
        weights — no second load, and never a Hugging Face download attempt.
        """
        runtime = find_mlx_runtime()
        if not runtime:
            raise RuntimeError(
                "mlx_lm not found. Install it (`pip install mlx-lm` in a Python "
                "3.10+ environment), then set mlx_python_path in Settings or "
                "APOLLO_MLX_PYTHON to that environment's python."
            )
        cwd = os.path.join(tempfile.gettempdir(), "apollo-mlx", m.id)
        os.makedirs(cwd, exist_ok=True)
        link = os.path.join(cwd, m.name)
        parser = getattr(m, "mlx_parser", None)
        if parser:
            _write_overlay(m.path, link, parser)
        elif os.path.realpath(link) != m.path:
            if os.path.islink(link):
                os.remove(link)
            elif os.path.isdir(link):
                shutil.rmtree(link)  # a previous launch's overlay
            os.symlink(m.path, link)
        # Chat mode sends no max_tokens on purpose (a fixed cap made reasoning
        # models spend it all thinking). llama-server treats that as
        # unlimited; mlx_lm.server falls back to 512, which cut Qwen-family
        # MLX models off mid-thought with an empty reply. Match llama.cpp.
        return runtime + ["--model", m.name, "--host", self._host,
                          "--port", str(port),
                          "--max-tokens", str(MLX_DEFAULT_MAX_TOKENS)], cwd

    def _llama_command(self, m: LocalModel, port: int) -> list[str]:
        binary = self.find_binary(m.arch)
        if not binary:
            configured = get_llama_server_path()
            if configured:
                raise RuntimeError(
                    f"Configured llama-server path does not exist: {configured}. "
                    "Fix it in Settings → AI → Local Models (or unset "
                    "APOLLO_LLAMA_SERVER to auto-detect)."
                )
            hint = (
                "winget install llama.cpp (or download a release build), then set "
                "the binary path in Settings → AI → Local Models"
                if os.name == "nt"
                else "e.g. `brew install llama.cpp`, or build it via the Cookbook"
            )
            raise RuntimeError(f"llama-server not found. Install llama.cpp ({hint}).")
        cmd = [
            binary, "--model", m.path,
            "--host", self._host, "--port", str(port),
            "-c", str(self._serving_context(m)),
        ]
        if m.kind == "embedding":
            cmd.append("--embedding")
        # A vision model needs its projector or llama-server loads fine and then
        # rejects every image with "image input is not supported". Guard on the
        # file still existing: a catalog entry can outlive the file, and a bad
        # --mmproj path fails the whole launch rather than just losing vision.
        mmproj = getattr(m, "mmproj", None)
        if mmproj and os.path.isfile(mmproj):
            cmd += ["--mmproj", mmproj]
        return cmd

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
                    f"llama-server exited early (code {proc.returncode}); "
                    f"full log: {log_path}\n{_tail(log_path)}"
                )
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.5)
        raise TimeoutError(
            f"llama-server did not become healthy in time; full log: {log_path}"
        )

    def _stop_proc(self, slot: _Proc) -> None:
        try:
            slot.proc.terminate()
            slot.proc.wait(timeout=10)
        except Exception as error:
            report_exception(
                logger,
                "local_model_terminate_failed",
                error,
                outcome="degraded",
                context={"model_id": slot.model_id},
            )
            try:
                slot.proc.kill()
            except Exception as cleanup_error:
                report_exception(
                    logger,
                    "local_model_kill_failed",
                    cleanup_error,
                    outcome="best_effort",
                    context={"model_id": slot.model_id},
                )
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


def _write_overlay(model_dir: str, overlay: str, parser: str) -> None:
    """Rebuild `overlay` as a folder of symlinks into `model_dir`, with a
    tokenizer_config.json copy that names the tool parser mlx_lm should use.
    The user's model files are never modified."""
    if os.path.realpath(overlay) == os.path.realpath(model_dir) and not os.path.islink(overlay):
        raise RuntimeError(f"refusing to rebuild overlay over the model folder itself: {model_dir}")
    if os.path.islink(overlay):
        os.remove(overlay)
    elif os.path.isdir(overlay):
        shutil.rmtree(overlay)
    os.makedirs(overlay)
    for entry in os.listdir(model_dir):
        if entry != "tokenizer_config.json":
            os.symlink(os.path.join(model_dir, entry), os.path.join(overlay, entry))
    cfg_path = os.path.join(model_dir, "tokenizer_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg["tool_parser_type"] = parser
    with open(os.path.join(overlay, "tokenizer_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _wait_mlx_loaded(base_url: str, name: str, log_path: str, timeout: float) -> None:
    """Block until mlx_lm.server has the weights in memory.

    Its /health answers "ok" before the model loads, so readiness is a
    one-token completion; a load failure (e.g. unsupported model_type) comes
    back as an error here instead of on the user's first message.
    """
    body = json.dumps({"model": name, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(base_url + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        status = repr(e)
    raise RuntimeError(
        f"mlx_lm.server could not load the model ({status}); full log: "
        f"{log_path}\n{_tail(log_path)}"
    )


def _probe_tool_calls(base_url: str) -> Optional[bool]:
    """Read chat_template_caps.supports_tool_calls from llama-server /props.

    Name heuristics get this wrong both ways: Hermes-3, Mistral-7B and
    Phi-3.5 GGUFs ship templates without tool support (they answer tool
    schemas in prose), while fine-tunes like "Qwopus" support tools but don't
    match any keyword. The template's own capabilities are authoritative.
    """
    try:
        with urllib.request.urlopen(base_url + "/props", timeout=5) as r:
            caps = json.loads(r.read()).get("chat_template_caps") or {}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    value = caps.get("supports_tool_calls")
    return value if isinstance(value, bool) else None


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
