"""HTTP API for local on-disk GGUF models.

All routes require admin: they enumerate the filesystem, change a global
scan-directory setting, and launch/kill OS processes — strictly more
privileged than the already admin-gated model-endpoint routes.
"""
from __future__ import annotations

from dataclasses import asdict
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.middleware import require_admin
from services.localmodels import lifecycle
from services.localmodels.scanner import scan_dirs, discover_piper_voices
from services.localmodels.config import (
    get_llama_server_path,
    get_local_context,
    get_local_kv_cache,
    get_local_model_dirs,
    set_llama_server_path,
    set_local_context,
    set_local_kv_cache,
    set_local_model_dirs,
)
from services.localmodels.server_manager import get_server
from src.observability import report_exception

logger = logging.getLogger(__name__)


class DirsBody(BaseModel):
    dirs: list[str]


class BinaryBody(BaseModel):
    path: str


class ContextBody(BaseModel):
    context: int


class KvCacheBody(BaseModel):
    kv_cache: str


def _restart_chat_models(server) -> list[str]:
    """Stop running chat models so the next message relaunches them with new
    launch settings; embedding models keep running. Returns the names."""
    return [info["name"] for mid, info in server.status().items()
            if info.get("kind") == "chat" and server.stop(mid)]


def _dir_status(raw: str, catalog) -> dict:
    """Per-directory state for Settings, so an unplugged drive reads as
    "not mounted" rather than looking configured-and-empty."""
    path = os.path.realpath(os.path.expanduser(raw))
    if os.path.isdir(path):
        count = sum(1 for m in catalog if m.directory == path)
        return {"path": raw, "state": "ok", "models": count}
    parts = path.split(os.sep)
    volume = os.sep.join(parts[:3]) if len(parts) > 2 and parts[1] == "Volumes" else ""
    if volume and not os.path.isdir(volume):
        return {"path": raw, "state": "unmounted", "models": 0}
    return {"path": raw, "state": "missing", "models": 0}


def _launch_error(error: Exception) -> dict:
    """Headline + log tail for a failed launch.

    llama-server already names the cause (e.g. "unknown model architecture:
    'k2-horizon'"); a fixed "could not be started" hid it. Admin-only route,
    so the tail's absolute paths are not a new disclosure.
    """
    text = str(error).strip()
    if not text:
        return {"error": "Model could not be started"}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    headline = next(
        (ln for ln in reversed(lines) if "error loading model" in ln.lower()),
        lines[0],
    )
    return {"error": headline[:300], "detail": text[-1500:]}


def setup_localmodels_routes() -> APIRouter:
    router = APIRouter(prefix="/api/local-models", tags=["local-models"])

    @router.get("")
    def list_models(request: Request):
        require_admin(request)
        server = get_server()
        status = server.status()
        catalog = scan_dirs(get_local_model_dirs())
        running_ids = set(status.keys())
        dirs = get_local_model_dirs()
        return {
            "dirs": dirs,
            "dir_status": [_dir_status(d, catalog) for d in dirs],
            "models": [
                {**asdict(m), "running": m.id in running_ids}
                for m in catalog
            ],
        }

    @router.post("/scan")
    def rescan(request: Request):
        require_admin(request)
        models = lifecycle.rescan()
        return {"count": len(models), "models": [asdict(m) for m in models]}

    @router.get("/voices")
    def list_voices(request: Request):
        """Piper TTS voices (*.onnx + sidecar) discovered in the model dirs."""
        require_admin(request)
        return {"voices": discover_piper_voices(get_local_model_dirs())}

    @router.get("/dirs")
    def get_dirs(request: Request):
        require_admin(request)
        return {"dirs": get_local_model_dirs()}

    @router.put("/dirs")
    def put_dirs(request: Request, body: DirsBody):
        require_admin(request)
        dirs = set_local_model_dirs(body.dirs)
        lifecycle.rescan()
        return {"dirs": dirs}

    @router.get("/binary")
    def get_binary(request: Request):
        """Configured llama-server path plus what actually resolves right now."""
        require_admin(request)
        return {
            "path": get_llama_server_path(),
            "resolved": get_server().find_binary() or "",
        }

    @router.put("/binary")
    def put_binary(request: Request, body: BinaryBody):
        require_admin(request)
        path = set_llama_server_path(body.path)
        return {
            "path": path,
            "resolved": get_server().find_binary() or "",
        }

    @router.get("/context")
    def get_context(request: Request):
        """Context window local llama.cpp models run with (0 = auto)."""
        require_admin(request)
        return {"context": get_local_context()}

    @router.put("/context")
    def put_context(request: Request, body: ContextBody):
        require_admin(request)
        try:
            context = set_local_context(body.context)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        # A running server keeps the window it started with; stop it so the
        # next message relaunches the model with the new size.
        return {"ok": True, "context": context, "restarted": _restart_chat_models(get_server())}

    @router.get("/kv-cache")
    def get_kv_cache(request: Request):
        """KV-cache precision llama.cpp chat models run with (q8_0 or f16)."""
        require_admin(request)
        return {"kv_cache": get_local_kv_cache()}

    @router.put("/kv-cache")
    def put_kv_cache(request: Request, body: KvCacheBody):
        require_admin(request)
        try:
            value = set_local_kv_cache(body.kv_cache)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return {"ok": True, "kv_cache": value, "restarted": _restart_chat_models(get_server())}

    @router.post("/{model_id}/start")
    def start(request: Request, model_id: str):
        require_admin(request)
        try:
            url = get_server().ensure_running(model_id)
            return {"ok": True, "base_url": url}
        except Exception as error:
            report_exception(
                logger,
                "local_model_start_failed",
                error,
                outcome="critical",
                context={"model_id": model_id},
            )
            return JSONResponse(
                {"ok": False, **_launch_error(error)}, status_code=400
            )

    @router.post("/{model_id}/stop")
    def stop(request: Request, model_id: str):
        require_admin(request)
        if get_server().stop(model_id):
            return {"ok": True}
        return {"ok": False, "error": "not running"}

    return router
