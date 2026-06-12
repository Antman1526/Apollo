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
import os
import re
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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
        return page

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
            except Exception as exc:
                self._record("navigation-error", str(exc), url)
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
            except Exception:
                try:
                    headers = response.headers if response else {}
                except Exception:
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
