"""Reverse proxy + status for the bundled Paperclip sidecar.

Mounted at /paperclip/* (HTTP) and /paperclip (websocket). The global
AuthMiddleware in app.py already gates /paperclip/* for HTTP. Websockets bypass
BaseHTTPMiddleware, so the websocket handler authenticates the session cookie
itself.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from services.paperclip.config import PaperclipConfig
from services.paperclip.proxy import (
    build_upstream_url,
    filter_request_headers,
    filter_response_headers,
)

logger = logging.getLogger(__name__)


def setup_paperclip_routes(
    cfg: PaperclipConfig,
    http_client: Optional[httpx.AsyncClient] = None,
    ws_validate: Optional[Callable[[Optional[str]], bool]] = None,
) -> APIRouter:
    router = APIRouter(tags=["paperclip"])
    client = http_client or httpx.AsyncClient(timeout=None)

    @router.get("/api/paperclip/status")
    async def status():
        # Server-side reachability ping (avoids browser CORS). Uses the
        # Apollo-reachable url, not the browser-facing one.
        reachable = None
        if cfg.enabled:
            try:
                r = await client.get(f"{cfg.url}/api/health", timeout=2.0)
                reachable = r.status_code < 500
            except Exception:
                reachable = False
        return {
            "enabled": cfg.enabled,
            "mode": cfg.mode,
            "url": cfg.url,
            "browser_url": cfg.browser_url,
            "model_endpoint": cfg.model_endpoint,
            "reachable": reachable,
        }

    @router.api_route(
        "/paperclip/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(path: str, request: Request):
        if not cfg.enabled:
            return JSONResponse({"detail": "Paperclip is disabled"}, status_code=503)
        url = build_upstream_url(cfg.url, path, request.url.query)
        headers = filter_request_headers(request.headers)
        body = await request.body()
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.ConnectError:
            return JSONResponse({"detail": "Paperclip is not reachable"}, status_code=502)
        resp_headers = filter_response_headers(upstream.headers)
        if request.method == "HEAD":
            await upstream.aclose()
            return Response(status_code=upstream.status_code, headers=resp_headers)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=BackgroundTask(upstream.aclose),
        )

    @router.websocket("/paperclip/{path:path}")
    async def proxy_ws(websocket, path: str):
        # Websockets bypass BaseHTTPMiddleware; authenticate here.
        from routes.auth_routes import SESSION_COOKIE  # local import: avoid cycle

        token = websocket.cookies.get(SESSION_COOKIE)
        ok = ws_validate(token) if ws_validate is not None else bool(token)
        if not cfg.enabled or not ok:
            await websocket.close(code=1008)  # policy violation
            return
        import websockets as _ws

        upstream_url = cfg.url.replace("http", "ws", 1) + "/" + path.lstrip("/")
        if websocket.url.query:
            upstream_url += "?" + websocket.url.query
        await websocket.accept()
        async with _ws.connect(upstream_url) as upstream:

            async def c2u():
                try:
                    while True:
                        await upstream.send(await websocket.receive_text())
                except Exception:
                    await upstream.close()

            async def u2c():
                try:
                    async for msg in upstream:
                        await websocket.send_text(msg)
                except Exception:
                    await websocket.close()

            await asyncio.gather(c2u(), u2c())

    return router
