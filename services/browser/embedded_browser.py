"""Agent-controllable browser session for Apollo.

Apollo is a FastAPI/static-web app rather than an Electron shell, so the
embedded UI panel is a sandboxed iframe while agent control uses a shared
Playwright Chromium page. The public contract mirrors the Electron
WebContentsView controls requested by the IDE browser blueprint.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import logging
import os
import re
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.observability import report_exception


logger = logging.getLogger(__name__)


BLOCKED_SCHEMES = {
    "about",
    "apollo",
    "chrome",
    "chrome-extension",
    "data",
    "devtools",
    "electron",
    "file",
    "javascript",
    "node",
    "vscode",
}
ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_TIMEOUT_MS = 15_000
MAX_SCRIPT_RESULT_CHARS = 20_000
MAX_SCRIPT_COLLECTION_ITEMS = 50
MAX_SCRIPT_RESULT_DEPTH = 4
LOCALHOST_RE = re.compile(
    r"(?P<url>(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{1,5})(?:/[^\s\"'<>]*)?)",
    re.IGNORECASE,
)
HOST_PORT_RE = re.compile(
    r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):\d{1,5}(?:[/?#].*)?$"
)


class BrowserUnavailable(RuntimeError):
    pass


class BrowserSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class BrowserEvent:
    id: int
    kind: str
    message: str
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "message": self.message, "url": self.url}


def is_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def status() -> dict[str, Any]:
    return {
        "available": is_available(),
        "package": "playwright",
        "engine": "chromium",
        "headless": os.getenv("APOLLO_BROWSER_HEADLESS", "true").lower() != "false",
        "install_hint": "pip install playwright && python -m playwright install chromium",
        "security": {
            "allowed_schemes": sorted(ALLOWED_SCHEMES),
            "blocked_schemes": sorted(BLOCKED_SCHEMES),
            "node_integration": False,
            "context_isolated": True,
            "sandboxed_ui_iframe": True,
        },
    }


def _ensure_valid_port(parsed) -> None:
    try:
        parsed.port
    except ValueError as exc:
        raise BrowserSecurityError("url port is invalid") from exc


def _looks_like_host_port(raw: str) -> bool:
    if not HOST_PORT_RE.match(raw):
        return False
    try:
        parsed = urlparse("http://" + raw)
        return parsed.port is not None
    except ValueError:
        return False


def normalize_url(raw_url: str) -> str:
    raw = (raw_url or "").strip()
    if not raw:
        raise BrowserSecurityError("url is required")
    if any(ch in raw for ch in ("\n", "\r", "\t")):
        raise BrowserSecurityError("url must be a single line")

    parsed = urlparse(raw)
    if parsed.scheme:
        scheme = parsed.scheme.lower()
        if scheme in BLOCKED_SCHEMES:
            raise BrowserSecurityError(f"blocked URL scheme: {scheme}")
        if "://" not in raw:
            if scheme not in ALLOWED_SCHEMES and _looks_like_host_port(raw):
                return "http://" + raw
            raise BrowserSecurityError(f"blocked URL scheme: {scheme}")
        if scheme not in ALLOWED_SCHEMES:
            raise BrowserSecurityError(f"blocked URL scheme: {scheme}")
        if not parsed.netloc:
            raise BrowserSecurityError("url must include a host")
        _ensure_valid_port(parsed)
        return raw

    if raw.startswith("//"):
        raise BrowserSecurityError("protocol-relative URLs are not allowed")
    normalized = "http://" + raw
    _ensure_valid_port(urlparse(normalized))
    return normalized


def security_warning(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return "non_secure_http"
    return ""


def _is_frameable(headers: dict) -> bool:
    """Can this response render inside a cross-origin iframe?

    False for X-Frame-Options DENY/SAMEORIGIN and for any CSP
    frame-ancestors directive other than '*'. Unknown/absent values are
    treated as frameable — let the iframe try rather than guess.
    """
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    xfo = lowered.get("x-frame-options", "").strip().lower()
    if xfo in ("deny", "sameorigin"):
        return False
    csp = lowered.get("content-security-policy", "")
    m = re.search(r"frame-ancestors([^;]*)", csp, re.I)
    if m:
        # Only a BARE * token allows any ancestor. Subdomain wildcards like
        # https://*.example.com still restrict embedding (yahoo.com ships a
        # long allowlist of those), so a substring check is not enough.
        sources = m.group(1).split()
        if "*" not in sources:
            return False
    return True


def detect_localhost_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in LOCALHOST_RE.finditer(text or ""):
        url = match.group("url").rstrip(".,);]")
        if not url.lower().startswith(("http://", "https://")):
            url = "http://" + url
        try:
            normalize_url(url)
        except BrowserSecurityError:
            continue
        if url not in found:
            found.append(url)
    return found


def _truncate_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_SCRIPT_RESULT_DEPTH:
        return "... (truncated nested result)"
    if isinstance(value, str) and len(value) > MAX_SCRIPT_RESULT_CHARS:
        return value[:MAX_SCRIPT_RESULT_CHARS] + f"\n... (truncated, {len(value)} chars total)"
    if isinstance(value, list):
        out = [_truncate_value(item, depth=depth + 1) for item in value[:MAX_SCRIPT_COLLECTION_ITEMS]]
        if len(value) > MAX_SCRIPT_COLLECTION_ITEMS:
            out.append(f"... (truncated, {len(value)} items total)")
        return out
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:MAX_SCRIPT_COLLECTION_ITEMS]:
            out[str(key)] = _truncate_value(item, depth=depth + 1)
        if len(items) > MAX_SCRIPT_COLLECTION_ITEMS:
            out["__truncated_keys__"] = len(items) - MAX_SCRIPT_COLLECTION_ITEMS
        return out
    return value


class EmbeddedBrowserSession:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._page = None
        self._event_seq = 0
        self._events: deque[BrowserEvent] = deque(maxlen=200)
        # Live-view (screencast / input) state. Kept OUTSIDE self._lock so the
        # CDP screencast-frame callback (which fires on Playwright's event loop
        # while navigate() may be holding self._lock during a page load) never
        # has to contend for the lock — taking it there would stall or deadlock
        # the frame stream.
        self._cdp = None            # active CDPSession bound to _screencast_page
        self._screencast_page = None  # the page the screencast is armed on
        self._on_frame = None       # caller's frame callback (data_b64, metadata)
        self._url_listeners: list = []  # framenavigated callbacks (main frame)
        self._listener_page = None  # page the url listeners are attached to

    def _record(self, kind: str, message: str, url: str = "") -> None:
        self._event_seq += 1
        self._events.append(BrowserEvent(id=self._event_seq, kind=kind, message=str(message), url=url))

    async def _ensure_page(self):
        if not is_available():
            raise BrowserUnavailable(status()["install_hint"])
        if self._page is not None and not self._page.is_closed():
            return self._page

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=os.getenv("APOLLO_BROWSER_HEADLESS", "true").lower() != "false",
        )
        context = await self._browser.new_context(
            ignore_https_errors=False,
            java_script_enabled=True,
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()
        page.on("console", lambda msg: self._record("console", f"{msg.type}: {msg.text}", page.url))
        page.on("pageerror", lambda exc: self._record("pageerror", str(exc), page.url))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self._page = page
        # A brand-new page invalidates any live-view bindings. Re-attach url
        # listeners immediately; re-arm the screencast if one was running. Both
        # run as fire-and-forget tasks so _ensure_page stays cheap and never
        # blocks page creation on CDP round-trips.
        self._attach_url_listeners(page)
        if self._on_frame is not None:
            asyncio.create_task(self._rearm_screencast(page))
        return page

    # ── Live view: screencast + input forwarding ──────────────────────────

    def _attach_url_listeners(self, page) -> None:
        """Wire ONE framenavigated handler on `page` that fans out to every
        registered url listener (main frame only). Idempotent per page: the
        handler is installed at most once, so re-registering a listener never
        double-fires."""
        if self._listener_page is page:
            return  # handler already installed on this page

        def _on_nav(frame):
            # Main-frame navigations only — subframe loads must not move the
            # address bar.
            if frame.parent_frame is not None:
                return
            for cb in list(self._url_listeners):
                try:
                    cb(frame.url)
                except Exception as error:
                    report_exception(
                        logger,
                        "embedded_browser_url_listener_failed",
                        error,
                        outcome="best_effort",
                    )

        page.on("framenavigated", _on_nav)
        self._listener_page = page

    def add_url_listener(self, cb) -> None:
        """Register cb(url) fired on main-frame navigation. Re-attached
        automatically when the page is recreated (see _ensure_page)."""
        if cb not in self._url_listeners:
            self._url_listeners.append(cb)
        if self._page is not None and not self._page.is_closed():
            self._attach_url_listeners(self._page)

    def remove_url_listener(self, cb) -> None:
        try:
            self._url_listeners.remove(cb)
        except ValueError:
            pass

    async def _rearm_screencast(self, page) -> None:
        """Restart the screencast on a freshly-created page. Tolerant of races
        (the page may already be gone / a newer page may have superseded it)."""
        if self._on_frame is None or page is not self._page:
            return
        try:
            await self._start_screencast_on(page)
        except Exception as error:  # never crash page creation
            report_exception(logger, "embedded_browser_screencast_rearm_failed", error, outcome="best_effort")
            self._record("screencast-error", "re-arm failed", page.url)

    async def _start_screencast_on(self, page) -> None:
        """Attach a CDP session to `page` and start a JPEG screencast. The
        per-frame callback acks immediately and forwards to self._on_frame
        WITHOUT touching self._lock."""
        # Tear down any stale CDP session first (page swap / restart).
        await self._detach_cdp()
        cdp = await page.context.new_cdp_session(page)

        def _on_screencast_frame(params: dict) -> None:
            session_id = params.get("sessionId")
            # Ack first so Chromium keeps sending frames; create_task because
            # the event fires synchronously on the loop.
            if session_id is not None:
                asyncio.create_task(self._ack_frame(cdp, session_id))
            on_frame = self._on_frame
            if on_frame is None:
                return
            metadata = params.get("metadata") or {}
            try:
                on_frame(params.get("data", ""), metadata)
            except Exception as error:
                report_exception(logger, "embedded_browser_screencast_viewer_callback_failed", error, outcome="best_effort")

        cdp.on("Page.screencastFrame", _on_screencast_frame)
        await cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 70,
                "maxWidth": 1280,
                "maxHeight": 1280,
                "everyNthFrame": 1,
            },
        )
        self._cdp = cdp
        self._screencast_page = page

    async def _ack_frame(self, cdp, session_id) -> None:
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception as error:
            report_exception(logger, "embedded_browser_screencast_ack_failed", error, outcome="best_effort")

    async def _detach_cdp(self) -> None:
        cdp, self._cdp, self._screencast_page = self._cdp, None, None
        if cdp is None:
            return
        try:
            await cdp.send("Page.stopScreencast")
        except Exception as error:
            report_exception(logger, "embedded_browser_screencast_stop_failed", error, outcome="best_effort")
        try:
            await cdp.detach()
        except Exception as error:
            report_exception(logger, "embedded_browser_cdp_detach_failed", error, outcome="best_effort")

    async def start_screencast(self, on_frame) -> None:
        """Begin streaming JPEG frames. `on_frame(data_b64, metadata)` is
        invoked per frame on Playwright's event loop and MUST be non-blocking.
        metadata carries deviceWidth/deviceHeight for input coordinate scaling.
        """
        self._on_frame = on_frame
        page = await self._ensure_page()
        await self._start_screencast_on(page)

    async def stop_screencast(self) -> None:
        self._on_frame = None
        await self._detach_cdp()

    # ── Input forwarding (mouse / keyboard) ───────────────────────────────

    def _live_page(self):
        """Page reference for input/screencast WITHOUT taking self._lock.

        Input must not deadlock against a navigate()/load that holds the lock;
        a click landing mid-load is harmless. We read the current page directly
        and rely on _ensure_page (under the lock) to have created one. If no
        page exists yet, raise BrowserUnavailable rather than blocking.
        """
        page = self._page
        if page is None or page.is_closed():
            raise BrowserUnavailable("browser page is not ready")
        return page

    async def input_mouse(self, kind, x, y, *, button="left", clicks=1, dx=0, dy=0) -> None:
        page = self._live_page()
        mouse = page.mouse
        if kind == "move":
            await mouse.move(x, y)
        elif kind == "down":
            await mouse.move(x, y)
            await mouse.down(button=button, click_count=clicks)
        elif kind == "up":
            await mouse.move(x, y)
            await mouse.up(button=button, click_count=clicks)
        elif kind == "click":
            await mouse.move(x, y)
            await mouse.down(button=button, click_count=clicks)
            await mouse.up(button=button, click_count=clicks)
        elif kind == "wheel":
            await mouse.wheel(dx, dy)
        else:
            raise ValueError(f"unknown mouse kind: {kind}")

    async def input_key(self, kind, key) -> None:
        page = self._live_page()
        if kind == "down":
            await page.keyboard.down(key)
        elif kind == "up":
            await page.keyboard.up(key)
        else:
            raise ValueError(f"unknown key kind: {kind}")

    # ── History navigation ────────────────────────────────────────────────

    async def _nav_result(self, page) -> dict[str, Any]:
        current = page.url
        return {"ok": True, "url": current, "title": await page.title()}

    async def go_back(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            await page.go_back(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            self._record("navigation", "go_back", page.url)
            return await self._nav_result(page)

    async def go_forward(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            await page.go_forward(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            self._record("navigation", "go_forward", page.url)
            return await self._nav_result(page)

    async def reload_page(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            await page.reload(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            self._record("navigation", "reload", page.url)
            return await self._nav_result(page)

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._browser = None
            self._playwright = None
            self._page = None

    async def navigate(self, raw_url: str) -> dict[str, Any]:
        url = normalize_url(raw_url)
        async with self._lock:
            page = await self._ensure_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
            except Exception as error:
                report_exception(logger, "embedded_browser_navigation_failed", error, outcome="critical")
                self._record("navigation-error", "navigation failed", url)
                raise
            current = page.url
            warning = security_warning(current)
            if warning:
                self._record("security-warning", warning, current)
            try:
                # all_headers() includes security headers delivered via the
                # network stack's extra-info; response.headers (the
                # preliminary set) misses e.g. yahoo's CSP entirely.
                headers = (await response.all_headers()) if response else {}
            except Exception as error:
                report_exception(logger, "embedded_browser_response_headers_read_failed", error, outcome="best_effort")
                try:
                    headers = response.headers if response else {}
                except Exception as fallback_error:
                    report_exception(logger, "embedded_browser_response_headers_fallback_failed", fallback_error, outcome="best_effort")
                    headers = {}
            return {
                "ok": True,
                "url": current,
                "title": await page.title(),
                "status": response.status if response else None,
                "warning": warning,
                # The Browser panel renders pages in a plain iframe; sites that
                # forbid framing (X-Frame-Options / CSP frame-ancestors) blank
                # out there. Tell the panel so it can fall back to an
                # agent-browser screenshot preview instead.
                "frameable": _is_frameable(headers),
            }

    async def get_current_url(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            return {"url": page.url, "title": await page.title()}

    async def get_page_html(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            return {"url": page.url, "html": await page.content()}

    async def get_visible_text(self) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            text = await page.locator("body").inner_text(timeout=DEFAULT_TIMEOUT_MS)
            return {"url": page.url, "text": text}

    async def execute_script(self, script: str) -> dict[str, Any]:
        if not (script or "").strip():
            raise ValueError("script is required")
        async with self._lock:
            page = await self._ensure_page()
            result = await page.evaluate(script)
            return {"url": page.url, "result": _truncate_value(result)}

    async def screenshot(self, *, full_page: bool = False) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            data = await page.screenshot(full_page=full_page, type="png")
            return {
                "url": page.url,
                "mime": "image/png",
                "base64": base64.b64encode(data).decode("ascii"),
            }

    async def wait_for_selector(self, selector: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
        if not (selector or "").strip():
            raise ValueError("selector is required")
        async with self._lock:
            page = await self._ensure_page()
            await page.wait_for_selector(selector, timeout=max(1000, min(int(timeout_ms), 60_000)))
            return {"ok": True, "url": page.url, "selector": selector}

    async def click(self, selector: str) -> dict[str, Any]:
        if not (selector or "").strip():
            raise ValueError("selector is required")
        async with self._lock:
            page = await self._ensure_page()
            await page.click(selector, timeout=DEFAULT_TIMEOUT_MS)
            return {"ok": True, "url": page.url, "selector": selector}

    async def type(self, selector: str, text: str) -> dict[str, Any]:
        if not (selector or "").strip():
            raise ValueError("selector is required")
        async with self._lock:
            page = await self._ensure_page()
            await page.fill(selector, text or "", timeout=DEFAULT_TIMEOUT_MS)
            return {"ok": True, "url": page.url, "selector": selector}

    def events(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in list(self._events)]


session = EmbeddedBrowserSession()
