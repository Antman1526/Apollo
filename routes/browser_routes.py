"""Browser panel and agent-control routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.browser import embedded_browser
from src.auth_helpers import require_privilege


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


def setup_browser_routes() -> APIRouter:
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

    return router
