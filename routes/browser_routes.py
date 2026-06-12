"""Browser panel and agent-control routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from services.browser import embedded_browser
from src.auth_helpers import require_privilege

logger = logging.getLogger(__name__)

# Module-level handle to the single active live-view connection. A new WS
# connection takes over the screencast from any previous one (last-one-wins),
# because the screencast streams a SHARED Playwright page and CDP only supports
# one screencast per page.
_current_viewer: "_LiveViewer | None" = None


class _FrameForwarder:
    """Forwards screencast frames to a WebSocket with strict backpressure.

    The CDP frame callback runs on the event loop and must never block. We keep
    AT MOST ONE send in flight: if a frame arrives while the previous send is
    still pending, the new frame is dropped (live view favours latency over
    completeness). Factored out for unit testing of the drop policy.
    """

    def __init__(self, send_text: Callable[[str], Any]) -> None:
        self._send_text = send_text
        self._inflight = False
        self.sent = 0
        self.dropped = 0

    def offer(self, message: str) -> bool:
        """Try to send `message`. Returns True if a send was scheduled, False
        if it was dropped because a send is still in flight."""
        if self._inflight:
            self.dropped += 1
            return False
        self._inflight = True
        asyncio.ensure_future(self._send(message))
        return True

    async def _send(self, message: str) -> None:
        try:
            await self._send_text(message)
            self.sent += 1
        except Exception:
            # Connection went away mid-send; the WS read loop will notice and
            # tear down. Just stop counting this as in-flight.
            pass
        finally:
            self._inflight = False


class _LiveViewer:
    """Owns the screencast + url listener lifecycle for one WS connection."""

    def __init__(self, websocket, session) -> None:
        self._ws = websocket
        self._session = session
        self._forwarder = _FrameForwarder(websocket.send_text)
        self._url_cb = None

    def _on_frame(self, data_b64: str, metadata: dict) -> None:
        # Runs on Playwright's event loop. Non-blocking: build the JSON and hand
        # it to the backpressure-aware forwarder, dropping if one is in flight.
        import json

        payload = json.dumps(
            {
                "type": "frame",
                "data": data_b64,
                "w": metadata.get("deviceWidth"),
                "h": metadata.get("deviceHeight"),
            }
        )
        self._forwarder.offer(payload)

    def _on_url(self, url: str) -> None:
        import json

        # title() is async; the address bar only needs the url here, so send it
        # immediately and let the client fetch title lazily if it wants.
        asyncio.ensure_future(
            self._safe_send(json.dumps({"type": "url", "url": url, "title": ""}))
        )

    async def _safe_send(self, message: str) -> None:
        try:
            await self._ws.send_text(message)
        except Exception:
            pass

    async def start(self) -> None:
        self._url_cb = self._on_url
        self._session.add_url_listener(self._url_cb)
        await self._session.start_screencast(self._on_frame)

    async def stop(self) -> None:
        try:
            await self._session.stop_screencast()
        except Exception:
            pass
        if self._url_cb is not None:
            self._session.remove_url_listener(self._url_cb)
            self._url_cb = None


class NavigateRequest(BaseModel):
    url: str = Field(..., min_length=1)


class ScriptRequest(BaseModel):
    script: str = Field(..., min_length=1)


class SelectorRequest(BaseModel):
    selector: str = Field(..., min_length=1)
    timeout_ms: int = Field(default=15_000, ge=1_000, le=60_000)


class TypeRequest(BaseModel):
    selector: str = Field(..., min_length=1)
    text: str = ""


class ScreenshotRequest(BaseModel):
    full_page: bool = False


class DetectRequest(BaseModel):
    text: str = ""


def _require_browser_privilege(request: Request) -> None:
    require_privilege(request, "can_use_browser")


def _handle_browser_error(exc: Exception) -> HTTPException:
    if isinstance(exc, embedded_browser.BrowserSecurityError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, embedded_browser.BrowserUnavailable):
        return HTTPException(503, str(exc))
    if isinstance(exc, TimeoutError) or exc.__class__.__name__ == "TimeoutError":
        return HTTPException(504, "Browser operation timed out")
    return HTTPException(500, f"Browser operation failed: {str(exc)[:240]}")


def setup_browser_routes(
    ws_validate: Optional[Callable[[Optional[str]], bool]] = None,
    ws_authorize: Optional[Callable[[Optional[str]], bool]] = None,
) -> APIRouter:
    """Build the browser router.

    ``ws_validate(token)`` → True when the session cookie is a valid session
    (mirrors the paperclip WS auth idiom). ``ws_authorize(token)`` → True when
    that session's user additionally holds the ``can_use_browser`` privilege.
    Both default to permissive when unset (single-user / auth-disabled), since
    websockets bypass the HTTP AuthMiddleware and must police themselves.
    """
    router = APIRouter(prefix="/api/browser", tags=["browser"])

    @router.get("/status")
    async def status(request: Request):
        _require_browser_privilege(request)
        return embedded_browser.status()

    @router.post("/navigate")
    async def navigate(body: NavigateRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.navigate(body.url)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.get("/current")
    async def current(request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.get_current_url()
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.get("/html")
    async def html(request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.get_page_html()
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.get("/text")
    async def text(request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.get_visible_text()
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.post("/execute")
    async def execute(body: ScriptRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.execute_script(body.script)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.post("/screenshot")
    async def screenshot(body: ScreenshotRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.screenshot(full_page=body.full_page)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.post("/wait")
    async def wait_for_selector(body: SelectorRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.wait_for_selector(body.selector, body.timeout_ms)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.post("/click")
    async def click(body: SelectorRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.click(body.selector)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.post("/type")
    async def type_text(body: TypeRequest, request: Request):
        _require_browser_privilege(request)
        try:
            return await embedded_browser.session.type(body.selector, body.text)
        except Exception as exc:
            raise _handle_browser_error(exc)

    @router.get("/events")
    async def events(request: Request):
        _require_browser_privilege(request)
        return {"events": embedded_browser.session.events()}

    @router.post("/detect-localhost")
    async def detect_localhost(body: DetectRequest, request: Request):
        _require_browser_privilege(request)
        urls = embedded_browser.detect_localhost_urls(body.text)
        return {"urls": urls, "count": len(urls)}

    @router.get("/tools")
    async def browser_tools(request: Request) -> dict[str, Any]:
        _require_browser_privilege(request)
        return {
            "tools": [
                "navigate",
                "getCurrentURL",
                "getPageHTML",
                "getVisibleText",
                "executeScript",
                "screenshot",
                "waitForSelector",
                "click",
                "type",
            ],
            "routes": {
                "navigate": "POST /api/browser/navigate",
                "getCurrentURL": "GET /api/browser/current",
                "getPageHTML": "GET /api/browser/html",
                "getVisibleText": "GET /api/browser/text",
                "executeScript": "POST /api/browser/execute",
                "screenshot": "POST /api/browser/screenshot",
                "waitForSelector": "POST /api/browser/wait",
                "click": "POST /api/browser/click",
                "type": "POST /api/browser/type",
            },
        }

    @router.websocket("/ws")
    async def browser_ws(websocket: WebSocket):
        # Websockets bypass BaseHTTPMiddleware (the HTTP AuthMiddleware), so we
        # authenticate the session cookie here exactly like the paperclip WS
        # proxy does, then additionally enforce the can_use_browser privilege.
        from routes.auth_routes import SESSION_COOKIE  # local import: avoid cycle

        token = websocket.cookies.get(SESSION_COOKIE)
        valid = ws_validate(token) if ws_validate is not None else True
        if not valid:
            await websocket.close(code=1008)  # policy violation
            return
        privileged = ws_authorize(token) if ws_authorize is not None else True
        if not privileged:
            await websocket.close(code=1008)
            return

        await websocket.accept()

        # Confirm the shared browser page is reachable before streaming. Use a
        # cheap call; surface unavailability as a protocol error + clean close
        # rather than a silent dead socket.
        import json

        from starlette.websockets import WebSocketDisconnect

        try:
            await embedded_browser.session.get_current_url()
        except embedded_browser.BrowserUnavailable as exc:
            await _ws_send(websocket, {"type": "error", "message": str(exc)})
            await websocket.close()
            return
        except Exception as exc:
            await _ws_send(
                websocket,
                {"type": "error", "message": f"browser unavailable: {str(exc)[:200]}"},
            )
            await websocket.close()
            return

        global _current_viewer
        # Single viewer: stop whatever the previous connection was streaming.
        previous = _current_viewer
        if previous is not None:
            await previous.stop()

        viewer = _LiveViewer(websocket, embedded_browser.session)
        _current_viewer = viewer
        try:
            await viewer.start()
        except Exception as exc:
            await _ws_send(
                websocket,
                {"type": "error", "message": f"screencast failed: {str(exc)[:200]}"},
            )
            await viewer.stop()
            if _current_viewer is viewer:
                _current_viewer = None
            await websocket.close()
            return

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    await _ws_send(websocket, {"type": "error", "message": "invalid JSON"})
                    continue
                try:
                    await _dispatch_ws_message(websocket, msg)
                except Exception as exc:
                    # Input/nav replay failures must never kill the stream.
                    logger.debug("browser ws message failed: %s", exc)
                    await _ws_send(
                        websocket,
                        {"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:200]}"},
                    )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug("browser ws closed: %s", exc)
        finally:
            await viewer.stop()
            if _current_viewer is viewer:
                _current_viewer = None

    return router


async def _ws_send(websocket, obj: dict) -> None:
    import json

    try:
        await websocket.send_text(json.dumps(obj))
    except Exception:
        pass


async def _dispatch_ws_message(websocket, msg: dict) -> None:
    """Route one client→server message to the session. Nav commands emit a
    follow-up {type:"url"} so the address bar stays in sync even though the
    framenavigated listener also fires."""
    session = embedded_browser.session
    kind = msg.get("type")

    if kind == "mouse":
        await session.input_mouse(
            msg.get("kind"),
            msg.get("x", 0),
            msg.get("y", 0),
            button=msg.get("button", "left"),
            clicks=int(msg.get("clicks", 1) or 1),
            dx=msg.get("dx", 0),
            dy=msg.get("dy", 0),
        )
    elif kind == "key":
        await session.input_key(msg.get("kind"), msg.get("key"))
    elif kind == "navigate":
        result = await session.navigate(msg.get("url", ""))
        await _ws_send(websocket, {"type": "url", "url": result.get("url"), "title": result.get("title")})
    elif kind == "back":
        result = await session.go_back()
        await _ws_send(websocket, {"type": "url", "url": result.get("url"), "title": result.get("title")})
    elif kind == "forward":
        result = await session.go_forward()
        await _ws_send(websocket, {"type": "url", "url": result.get("url"), "title": result.get("title")})
    elif kind == "reload":
        result = await session.reload_page()
        await _ws_send(websocket, {"type": "url", "url": result.get("url"), "title": result.get("title")})
    else:
        await _ws_send(websocket, {"type": "error", "message": f"unknown message type: {kind}"})
