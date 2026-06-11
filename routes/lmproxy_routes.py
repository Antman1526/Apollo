"""Stable, localhost OpenAI-compatible proxy in front of Apollo's warm local
model (llama-server).

Paperclip's opencode-local agents are pointed at `…/lmproxy/v1` with a bearer
token (passed as OPENAI_API_KEY), so they run on exactly the GGUF model Apollo
serves from the user's configured local-models folder. The route is auth-exempt
from Apollo's session middleware (added to AUTH_EXEMPT_PREFIXES) and guarded by
the token instead, so a same-host child process can reach it without a login.
"""
from __future__ import annotations

import hmac
import logging
import time
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
    agent_lookup: Optional[Callable[[str], Optional[dict]]] = None,
    publish_activity: Optional[Callable[[list], int]] = None,
    pulse_interval: float = 10.0,
) -> APIRouter:
    router = APIRouter(tags=["lmproxy"])
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=None)
    if owns_client:
        # include_router propagates this to the app's shutdown hooks.
        router.add_event_handler("shutdown", client.aclose)
    last_pulse: dict[str, float] = {}

    def _resolve_actor(request: Request) -> Optional[dict]:
        """Map the bearer token to an actor: the shared proxy token, or a
        per-agent token minted for Paperclip attribution (Phase 3.4)."""
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if not token:
            return None
        expected = token_provider()
        if expected and hmac.compare_digest(token, expected):
            return {"agent_id": "", "name": ""}
        if agent_lookup:
            meta = agent_lookup(token)
            if meta and meta.get("agent_id"):
                return {"agent_id": str(meta["agent_id"]), "name": str(meta.get("name", ""))}
        return None

    def _pulse(actor: dict) -> None:
        """Show the agent visibly working on the Floor — at most one event per
        agent per pulse_interval so token streams don't flood the hub."""
        agent_id = actor.get("agent_id")
        if not agent_id or publish_activity is None:
            return
        now = time.monotonic()
        if now - last_pulse.get(agent_id, float("-inf")) < pulse_interval:
            return
        last_pulse[agent_id] = now
        try:
            publish_activity([{
                "type": "heartbeat.run.event",
                "payload": {
                    "agentId": agent_id,
                    "name": actor.get("name") or agent_id,
                    "tool": "llm",
                },
                "received_at": time.time(),
            }])
        except Exception as exc:  # never fail the proxy over a viz event
            logger.debug("lmproxy activity pulse failed: %s", exc)

    async def _forward(request: Request, subpath: str):
        actor = _resolve_actor(request)
        if actor is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        _pulse(actor)
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
