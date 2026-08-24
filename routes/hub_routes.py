"""HTTP API for the model hub: free cloud models, HF GGUF pulls, Codex Router.

Admin-only throughout: these routes create model endpoints (with API keys),
write files into the local-models directories, and report on local services.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import ModelEndpoint, SessionLocal
from core.middleware import require_admin
from services.model_hub import (
    PROVIDERS,
    codex_router_status,
    download_status,
    list_free_models,
    list_gguf_files,
    search_gguf_repos,
    start_gguf_download,
)

logger = logging.getLogger(__name__)


class FreeEndpointBody(BaseModel):
    provider: str
    api_key: str


class GgufDownloadBody(BaseModel):
    repo_id: str
    file: str
    hf_token: Optional[str] = None


def setup_hub_routes() -> APIRouter:
    router = APIRouter(prefix="/api/hub", tags=["model-hub"])

    @router.get("/free-models")
    def free_models(request: Request, provider: str):
        require_admin(request)
        try:
            return {"provider": provider, "models": list_free_models(provider)}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.warning("free-models fetch failed for %s: %s", provider, e)
            raise HTTPException(502, f"Could not reach {provider}")

    @router.post("/free-endpoint")
    def add_free_endpoint(request: Request, body: FreeEndpointBody):
        """Create (or refresh) a ModelEndpoint scoped to a provider's free models.

        cached_models is pinned to the current free list so the picker shows
        only $0 models — the whole point of the endpoint — instead of the
        provider's full paid catalog.
        """
        require_admin(request)
        if body.provider not in PROVIDERS:
            raise HTTPException(400, f"provider must be one of {sorted(PROVIDERS)}")
        if not body.api_key.strip():
            raise HTTPException(400, "api_key is required (free models still need a provider key)")
        try:
            free = list_free_models(body.provider)
        except Exception as e:
            logger.warning("free-endpoint list failed for %s: %s", body.provider, e)
            raise HTTPException(502, f"Could not fetch the {body.provider} model list")
        if not free:
            raise HTTPException(502, f"{body.provider} reported no free models")
        cfg = PROVIDERS[body.provider]
        db = SessionLocal()
        try:
            ep = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == cfg["base_url"])
                .first()
            )
            if ep is None:
                ep = ModelEndpoint(id=str(uuid.uuid4()), name=cfg["label"],
                                   base_url=cfg["base_url"], model_type="llm")
                db.add(ep)
            ep.api_key = body.api_key.strip()
            ep.is_enabled = True
            ep.cached_models = json.dumps([m["id"] for m in free])
            db.commit()
            return {"ok": True, "endpoint_id": ep.id, "name": ep.name,
                    "free_models": len(free)}
        finally:
            db.close()

    @router.get("/gguf-search")
    def gguf_search(request: Request, q: str, limit: int = 12):
        require_admin(request)
        if not q.strip():
            raise HTTPException(400, "q is required")
        try:
            return {"repos": search_gguf_repos(q.strip(), limit=limit)}
        except Exception as e:
            logger.warning("gguf search failed: %s", e)
            raise HTTPException(502, "Hugging Face search failed")

    @router.get("/gguf-files")
    def gguf_files(request: Request, repo: str):
        require_admin(request)
        try:
            return {"repo": repo, "files": list_gguf_files(repo)}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.warning("gguf file list failed: %s", e)
            raise HTTPException(502, "Could not list repo files")

    @router.post("/gguf-download")
    def gguf_download(request: Request, body: GgufDownloadBody):
        """Pull one GGUF into the first local-models scan dir; scanner rescans on finish."""
        require_admin(request)
        from services.localmodels.config import get_local_model_dirs
        import os
        dirs = get_local_model_dirs()
        dest = next((d for d in dirs if os.path.isdir(d)), None)
        if not dest:
            raise HTTPException(
                400,
                "No local model directory exists yet — add one under "
                "Settings → AI → Local Models first.",
            )
        result = start_gguf_download(
            body.repo_id, body.file, dest, hf_token=(body.hf_token or "").strip()
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "download refused"))
        return result

    @router.get("/gguf-downloads")
    def gguf_downloads(request: Request):
        require_admin(request)
        return {"downloads": download_status()}

    @router.get("/codex-router")
    def codex_router(request: Request):
        require_admin(request)
        return codex_router_status()

    return router
