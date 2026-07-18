"""Reverse proxy + status for the bundled Paperclip sidecar.

Mounted at /paperclip/* (HTTP) and /paperclip (websocket). The global
AuthMiddleware in app.py already gates /paperclip/* for HTTP. Websockets bypass
BaseHTTPMiddleware, so the websocket handler authenticates the session cookie
itself.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from services.paperclip.config import PaperclipConfig
from services.paperclip.events import EventHub, FLOOR_EVENT_TYPES
from services.paperclip.proxy import (
    build_upstream_url,
    filter_request_headers,
    filter_response_headers,
)
from services.paperclip import browser_use_verifier
from services.integrations import agent_workbench
from src.observability import report_exception

logger = logging.getLogger(__name__)

_MAX_INGEST_BATCH = 100


def setup_paperclip_routes(
    cfg: PaperclipConfig,
    http_client: Optional[httpx.AsyncClient] = None,
    ws_validate: Optional[Callable[[Optional[str]], bool]] = None,
    hub: Optional[EventHub] = None,
    collector_status: Optional[Callable[[], dict]] = None,
    agent_tokens=None,
) -> APIRouter:
    router = APIRouter(tags=["paperclip"])
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=None)
    if owns_client:
        # include_router propagates this to the app's shutdown hooks.
        router.add_event_handler("shutdown", client.aclose)
    hub = hub or EventHub()
    events_token = os.environ.get("PAPERCLIP_EVENTS_TOKEN", "")

    async def build_status(app_base_url: str | None = None):
        # Server-side reachability ping (avoids browser CORS). Uses the
        # Apollo-reachable url, not the browser-facing one.
        reachable = None
        if cfg.enabled:
            try:
                r = await client.get(f"{cfg.url}/api/health", timeout=2.0)
                reachable = r.status_code < 500
            except Exception as error:
                report_exception(
                    logger,
                    "paperclip_health_probe_failed",
                    error,
                    outcome="degraded",
                )
                reachable = False
        browser_use = browser_use_verifier.status(app_base_url)
        payload = {
            "enabled": cfg.enabled,
            "mode": cfg.mode,
            "url": cfg.url,
            "browser_url": cfg.browser_url,
            "model_endpoint": cfg.model_endpoint,
            "reachable": reachable,
            "browser_use": browser_use,
            "collector": collector_status() if collector_status else None,
        }
        payload["agent_workbench"] = agent_workbench.status(
            app_base_url=app_base_url,
            paperclip_status=payload,
            browser_use_status=browser_use,
        )
        return payload

    @router.get("/api/paperclip/status")
    async def status(request: Request):
        return await build_status(str(request.base_url).rstrip("/"))

    @router.post("/api/paperclip/events")
    async def ingest_events(request: Request):
        """Ingest agent activity (e.g. the DeeperCode Ralph loop) for the Floor.

        Exempted from session auth in app.py (same pattern as task webhooks):
        the handler proves identity itself. When PAPERCLIP_EVENTS_TOKEN is set
        the X-Paperclip-Events-Token header must match; otherwise only
        loopback clients are accepted.
        """
        if events_token:
            provided = request.headers.get("x-paperclip-events-token", "")
            if not hmac.compare_digest(provided, events_token):
                return JSONResponse({"detail": "invalid events token"}, status_code=401)
        else:
            # Loopback-only trust is void behind a reverse proxy (client.host
            # becomes the proxy), so refuse proxied requests in tokenless mode.
            client_host = request.client.host if request.client else ""
            if client_host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
                return JSONResponse(
                    {"detail": "remote ingest requires PAPERCLIP_EVENTS_TOKEN"},
                    status_code=401,
                )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"detail": "invalid JSON"}, status_code=400)

        raw_events = body.get("events") if isinstance(body, dict) else None
        if raw_events is None and isinstance(body, dict) and "type" in body:
            raw_events = [body]
        if not isinstance(raw_events, list) or not raw_events:
            return JSONResponse({"detail": "expected {events: [...]}"}, status_code=400)
        if len(raw_events) > _MAX_INGEST_BATCH:
            return JSONResponse(
                {"detail": f"batch too large (max {_MAX_INGEST_BATCH})"}, status_code=413
            )

        valid = []
        for event in raw_events:
            if (
                isinstance(event, dict)
                and event.get("type") in FLOOR_EVENT_TYPES
                and isinstance(event.get("payload"), dict)
            ):
                valid.append(
                    {
                        "type": event["type"],
                        "payload": event["payload"],
                        "received_at": time.time(),
                    }
                )
        accepted = hub.publish(valid)
        return {"accepted": accepted, "rejected": len(raw_events) - accepted}

    @router.get("/api/paperclip/stream")
    async def stream():
        """UI-facing SSE stream for the Apollo-native Paperclip Floor.

        Backed by the ingest hub (/api/paperclip/events). Replays the recent
        buffer on connect, then streams live events with keepalive comments.
        When the sidecar is disabled and nothing has been ingested, emits the
        legacy `paperclip.stream.unavailable` event so the Floor falls back to
        preview mode. When enabled but idle it emits `paperclip.stream.waiting`
        and holds the connection so the Floor goes live as soon as agent
        activity starts.
        """

        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

        async def events():
            if not cfg.enabled and not hub.recent:
                yield sse({
                    "type": "paperclip.stream.unavailable",
                    "payload": {"reason": "disabled"},
                })
                return

            # Subscribe before snapshotting the replay buffer so events
            # published in between are not lost; the seq watermark drops the
            # ones that show up in both.
            queue = hub.subscribe()
            try:
                recent = hub.recent
                last_seq = recent[-1][0] if recent else 0
                if not recent:
                    yield sse({
                        "type": "paperclip.stream.waiting",
                        "payload": {"reason": "no_events_yet"},
                    })
                for _seq, event in recent:
                    yield sse(event)
                while True:
                    try:
                        seq, event = await asyncio.wait_for(queue.get(), timeout=25.0)
                        if seq <= last_seq:
                            continue
                        yield sse(event)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/paperclip/agent-tokens")
    async def mint_agent_token(request: Request):
        """Mint (or rotate) a per-agent lmproxy token (Phase 3.4).

        Paste the returned token into the Paperclip agent's opencode-local
        config as OPENAI_API_KEY; the proxy then attributes that agent's LLM
        calls and pulses its activity onto the Floor.
        """
        from core.middleware import require_admin  # local import: keep module light

        require_admin(request)
        if agent_tokens is None:
            return JSONResponse({"detail": "agent tokens not configured"}, status_code=503)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"detail": "invalid JSON"}, status_code=400)
        agent_id = str((body or {}).get("agent_id", "")).strip()
        if not agent_id:
            return JSONResponse({"detail": "agent_id is required"}, status_code=400)
        name = str((body or {}).get("name", "")).strip()
        token = agent_tokens.mint(agent_id, name)
        return {"agent_id": agent_id, "name": name or agent_id, "token": token}

    @router.get("/api/paperclip/agent-tokens")
    def list_agent_tokens(request: Request):
        from core.middleware import require_admin  # local import: keep module light

        require_admin(request)
        if agent_tokens is None:
            return {"tokens": []}
        return {"tokens": agent_tokens.list()}

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
        except httpx.RequestError as exc:
            return JSONResponse(
                {"detail": f"Paperclip request failed: {exc.__class__.__name__}"},
                status_code=502,
            )
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
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    report_exception(
                        logger,
                        "paperclip_websocket_client_forward_failed",
                        error,
                        outcome="degraded",
                    )
                    await upstream.close()

            async def u2c():
                try:
                    async for msg in upstream:
                        await websocket.send_text(msg)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    report_exception(
                        logger,
                        "paperclip_websocket_upstream_forward_failed",
                        error,
                        outcome="degraded",
                    )
                    await websocket.close()

            await asyncio.gather(c2u(), u2c())

    return router
