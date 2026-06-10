"""Stable, localhost OpenAI-compatible proxy in front of Apollo's warm local
model (llama-server).

Paperclip's opencode-local agents are pointed at `…/lmproxy/v1` with a bearer
token (passed as OPENAI_API_KEY), so they run on exactly the GGUF model Apollo
serves from the user's configured local-models folder. The route is auth-exempt
from Apollo's session middleware (added to AUTH_EXEMPT_PREFIXES) and guarded by
the token instead, so a same-host child process can reach it without a login.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from services.paperclip.proxy import filter_request_headers, filter_response_headers

logger = logging.getLogger(__name__)


def setup_lmproxy_routes(
    token_provider: Callable[[], str],
    warm_url_provider: Callable[[], Optional[str]],
    http_client: Optional[httpx.AsyncClient] = None,
) -> APIRouter:
    router = APIRouter(tags=["lmproxy"])
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=None)
    if owns_client:
        # include_router propagates this to the app's shutdown hooks.
        router.add_event_handler("shutdown", client.aclose)

    def _authed(request: Request) -> bool:
        expected = token_provider()
        if not expected:
            return False
        auth = request.headers.get("authorization", "")
        return auth.strip() == f"Bearer {expected}"

    async def _forward(request: Request, subpath: str):
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        warm = warm_url_provider()
        if not warm:
            return JSONResponse(
                {"error": "no local model is currently running; serve one in "
                          "Apollo (Settings → AI / model picker) first"},
                status_code=503,
            )
        url = f"{warm.rstrip('/')}/v1/{subpath.lstrip('/')}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = filter_request_headers(request.headers)
        # Drop the inbound bearer; the warm llama-server needs no auth.
        headers.pop("authorization", None)
        headers.pop("Authorization", None)
        body = await request.body()
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.ConnectError:
            return JSONResponse({"error": "local model server unreachable"}, status_code=502)
        except httpx.RequestError as exc:
            return JSONResponse(
                {"error": f"local model request failed: {exc.__class__.__name__}"},
                status_code=502,
            )
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=filter_response_headers(upstream.headers),
            background=BackgroundTask(upstream.aclose),
        )

    @router.get("/lmproxy/v1/models")
    async def models(request: Request):
        return await _forward(request, "models")

    @router.api_route("/lmproxy/v1/{path:path}", methods=["POST", "GET"])
    async def passthrough(path: str, request: Request):
        return await _forward(request, path)

    return router
