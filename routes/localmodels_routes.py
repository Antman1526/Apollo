"""HTTP API for local on-disk GGUF models."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.localmodels import lifecycle
from services.localmodels.scanner import scan_dirs
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
        catalog = scan_dirs(get_local_model_dirs())
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
