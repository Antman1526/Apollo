"""Model hub: free cloud models (OpenRouter / OpenCode Zen) + HF GGUF pulls.

Two jobs, both feeding existing Apollo machinery rather than new stores:

1. Free cloud catalogs — query a provider's OpenAI-compatible ``/models``,
   keep only the $0 entries, and hand back ids the routes layer turns into a
   normal ModelEndpoint row (so the picker, fallbacks, and roles all work).

2. LM Studio-style pulls — search Hugging Face for GGUF repos, list a repo's
   .gguf files, and stream one into the first configured local-models dir,
   where the existing scanner picks it up on rescan.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter (Free)",
        "base_url": "https://openrouter.ai/api/v1",
        "models_url": "https://openrouter.ai/api/v1/models",
    },
    "opencode": {
        "label": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "models_url": "https://opencode.ai/zen/v1/models",
    },
}

_CACHE_TTL = 900.0
_free_cache: Dict[str, tuple] = {}  # provider -> (ts, models)

# ── Codex Router (github.com/duolahypercho/codex-router) ──
# A local gateway that gives the OpenAI Codex CLI access to 20+ providers
# (OpenRouter free, OpenCode free, Ollama, …). It speaks ONLY the Responses
# API behind per-caller capability tokens, so Apollo cannot consume it as a
# chat-completions endpoint — Apollo's role is detection + guided install,
# while Apollo's own free models come direct from the providers above.
CODEX_ROUTER_PORT = 4202

CODEX_ROUTER_INSTALL = {
    "darwin": (
        "brew tap duolahypercho/codex-router https://github.com/duolahypercho/codex-router\n"
        "brew install codex-router\n"
        "codex-router setup --guided"
    ),
    "windows": (
        '$installer = Join-Path $env:TEMP "codex-router-install.ps1"\n'
        "Invoke-WebRequest https://raw.githubusercontent.com/duolahypercho/codex-router/main/install.ps1 -OutFile $installer\n"
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Target codex -Guided"
    ),
    "linux": (
        "curl -fsSL https://raw.githubusercontent.com/duolahypercho/codex-router/main/install.sh "
        "| sh -s -- --target codex --guided"
    ),
}


def codex_router_status() -> Dict[str, Any]:
    """Is a codex-router listening on its loopback port? Plus install help."""
    import platform
    import socket

    running = False
    try:
        with socket.create_connection(("127.0.0.1", CODEX_ROUTER_PORT), timeout=0.5):
            running = True
    except OSError:
        running = False
    system = platform.system().lower()
    key = "darwin" if system == "darwin" else ("windows" if system == "windows" else "linux")
    return {
        "running": running,
        "port": CODEX_ROUTER_PORT,
        "platform": key,
        "install_commands": CODEX_ROUTER_INSTALL[key],
        "note": (
            "Codex Router serves the OpenAI Codex CLI (Responses API only) — "
            "its models appear in Codex, not in Apollo's picker. For free "
            "models inside Apollo, add the OpenRouter/OpenCode endpoints below."
        ),
    }


HF_API = "https://huggingface.co/api"
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
# repo-relative gguf path: subdirs allowed, traversal not.
_GGUF_FILE_RE = re.compile(r"^(?!.*\.\.)[\w./ +-]+\.gguf$", re.IGNORECASE)


def _is_free(model: Dict[str, Any]) -> bool:
    pricing = model.get("pricing") or {}
    if pricing:
        try:
            return float(pricing.get("prompt") or 0) == 0 and \
                   float(pricing.get("completion") or 0) == 0
        except (TypeError, ValueError):
            return False
    # Providers without pricing metadata (Zen today): trust the id/name marker.
    ident = f"{model.get('id', '')} {model.get('name', '')}".lower()
    return "free" in ident


def list_free_models(provider: str, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Free models for a provider: [{id, name, context_length}], cached 15 min."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    cached = _free_cache.get(provider)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    import httpx
    resp = httpx.get(PROVIDERS[provider]["models_url"], timeout=timeout,
                     headers={"User-Agent": "apollo-model-hub"})
    resp.raise_for_status()
    payload = resp.json()
    raw = payload.get("data") if isinstance(payload, dict) else payload
    out = []
    for m in raw or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        if _is_free(m):
            out.append({
                "id": m["id"],
                "name": m.get("name") or m["id"],
                "context_length": m.get("context_length"),
            })
    _free_cache[provider] = (time.time(), out)
    return out


def search_gguf_repos(query: str, limit: int = 12, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Search HF for GGUF model repos, most-downloaded first."""
    import httpx
    resp = httpx.get(
        f"{HF_API}/models",
        params={"search": query, "filter": "gguf", "sort": "downloads",
                "direction": "-1", "limit": str(max(1, min(limit, 30)))},
        timeout=timeout, headers={"User-Agent": "apollo-model-hub"},
    )
    resp.raise_for_status()
    return [
        {"repo_id": m.get("modelId") or m.get("id"),
         "downloads": m.get("downloads"), "likes": m.get("likes")}
        for m in resp.json() if isinstance(m, dict)
    ]


def list_gguf_files(repo_id: str, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """List .gguf files (path + size) in an HF repo."""
    if not _REPO_RE.match(repo_id or ""):
        raise ValueError("invalid repo id")
    import httpx
    resp = httpx.get(f"{HF_API}/models/{repo_id}/tree/main",
                     params={"recursive": "true"}, timeout=timeout,
                     headers={"User-Agent": "apollo-model-hub"})
    resp.raise_for_status()
    return [
        {"path": f["path"], "size_bytes": f.get("size")}
        for f in resp.json()
        if isinstance(f, dict) and str(f.get("path", "")).lower().endswith(".gguf")
    ]


class _Download:
    def __init__(self, repo_id: str, file_path: str, dest: str):
        self.repo_id = repo_id
        self.file_path = file_path
        self.dest = dest
        self.done_bytes = 0
        self.total_bytes: Optional[int] = None
        self.status = "downloading"  # downloading | done | error
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id, "file": self.file_path, "dest": self.dest,
            "done_bytes": self.done_bytes, "total_bytes": self.total_bytes,
            "status": self.status, "error": self.error,
        }


_downloads: Dict[str, _Download] = {}
_dl_lock = threading.Lock()


def download_status() -> List[Dict[str, Any]]:
    with _dl_lock:
        return [d.to_dict() for d in _downloads.values()]


def start_gguf_download(repo_id: str, file_path: str, dest_dir: str,
                        hf_token: str = "") -> Dict[str, Any]:
    """Stream one .gguf from HF into dest_dir on a worker thread.

    The URL is constructed server-side from validated repo/file parts only —
    a caller can never point this at another host or traverse out of the
    destination directory. Partial files use a .part suffix and are renamed
    only on completion, so the scanner never sees a half model.
    """
    if not _REPO_RE.match(repo_id or ""):
        return {"ok": False, "error": "invalid repo id"}
    if not _GGUF_FILE_RE.match(file_path or ""):
        return {"ok": False, "error": "invalid gguf file path"}
    if not os.path.isdir(dest_dir):
        return {"ok": False, "error": f"destination is not a directory: {dest_dir}"}
    dest = os.path.join(dest_dir, os.path.basename(file_path))
    key = f"{repo_id}/{file_path}"
    with _dl_lock:
        existing = _downloads.get(key)
        if existing and existing.status == "downloading":
            return {"ok": False, "error": "already downloading"}
        dl = _Download(repo_id, file_path, dest)
        _downloads[key] = dl

    def _run():
        import httpx
        url = f"https://huggingface.co/{repo_id}/resolve/main/{file_path}"
        headers = {"User-Agent": "apollo-model-hub"}
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"
        tmp = dest + ".part"
        try:
            with httpx.stream("GET", url, headers=headers, timeout=60.0,
                              follow_redirects=True) as resp:
                resp.raise_for_status()
                length = resp.headers.get("content-length")
                dl.total_bytes = int(length) if length else None
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1 << 20):
                        f.write(chunk)
                        dl.done_bytes += len(chunk)
            os.replace(tmp, dest)
            dl.status = "done"
            try:
                from services.localmodels import lifecycle
                lifecycle.rescan()
            except Exception:
                logger.debug("post-download rescan failed", exc_info=True)
        except Exception as e:
            dl.status = "error"
            dl.error = str(e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    threading.Thread(target=_run, name=f"gguf-dl-{repo_id}", daemon=True).start()
    return {"ok": True, "key": key, "dest": dest}
