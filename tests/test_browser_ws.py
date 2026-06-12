"""Tests for the embedded-browser live view: input mapping, the WS frame
forwarder backpressure policy, WS auth gating, and the WS protocol round-trip.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from routes.browser_routes import _FrameForwarder, setup_browser_routes
from services.browser import embedded_browser


# ── input_mouse / input_key dispatch to a stubbed page ────────────────────


class _RecordingMouse:
    def __init__(self):
        self.calls = []

    async def move(self, x, y):
        self.calls.append(("move", x, y))

    async def down(self, *, button="left", click_count=1):
        self.calls.append(("down", button, click_count))

    async def up(self, *, button="left", click_count=1):
        self.calls.append(("up", button, click_count))

    async def wheel(self, dx, dy):
        self.calls.append(("wheel", dx, dy))


class _RecordingKeyboard:
    def __init__(self):
        self.calls = []

    async def down(self, key):
        self.calls.append(("down", key))

    async def up(self, key):
        self.calls.append(("up", key))


class _FakePage:
    def __init__(self):
        self.mouse = _RecordingMouse()
        self.keyboard = _RecordingKeyboard()

    def is_closed(self):
        return False


def _session_with_page(page):
    session = embedded_browser.EmbeddedBrowserSession()
    session._page = page
    return session


async def test_input_mouse_move():
    page = _FakePage()
    session = _session_with_page(page)
    await session.input_mouse("move", 10, 20)
    assert page.mouse.calls == [("move", 10, 20)]


async def test_input_mouse_click_moves_then_down_up():
    page = _FakePage()
    session = _session_with_page(page)
    await session.input_mouse("click", 5, 6, button="left", clicks=2)
    assert page.mouse.calls == [
        ("move", 5, 6),
        ("down", "left", 2),
        ("up", "left", 2),
    ]


async def test_input_mouse_down_and_up_move_first():
    page = _FakePage()
    session = _session_with_page(page)
    await session.input_mouse("down", 1, 2, button="right")
    await session.input_mouse("up", 1, 2, button="right")
    assert page.mouse.calls == [
        ("move", 1, 2),
        ("down", "right", 1),
        ("move", 1, 2),
        ("up", "right", 1),
    ]


async def test_input_mouse_wheel():
    page = _FakePage()
    session = _session_with_page(page)
    await session.input_mouse("wheel", 0, 0, dx=3, dy=120)
    assert page.mouse.calls == [("wheel", 3, 120)]


async def test_input_mouse_unknown_kind_raises():
    session = _session_with_page(_FakePage())
    with pytest.raises(ValueError):
        await session.input_mouse("teleport", 0, 0)


async def test_input_key_down_up():
    page = _FakePage()
    session = _session_with_page(page)
    await session.input_key("down", "Shift")
    await session.input_key("up", "Shift")
    assert page.keyboard.calls == [("down", "Shift"), ("up", "Shift")]


async def test_input_key_unknown_kind_raises():
    session = _session_with_page(_FakePage())
    with pytest.raises(ValueError):
        await session.input_key("press", "a")


async def test_input_without_page_raises_unavailable():
    session = embedded_browser.EmbeddedBrowserSession()  # no page
    with pytest.raises(embedded_browser.BrowserUnavailable):
        await session.input_mouse("move", 0, 0)


# ── history navigation returns nav-result shape ───────────────────────────


class _NavPage(_FakePage):
    def __init__(self):
        super().__init__()
        self.url = "http://example.com/"
        self.ops = []

    async def go_back(self, **kw):
        self.ops.append("back")
        self.url = "http://example.com/prev"

    async def go_forward(self, **kw):
        self.ops.append("forward")
        self.url = "http://example.com/next"

    async def reload(self, **kw):
        self.ops.append("reload")

    async def title(self):
        return "T"


async def test_go_back_forward_reload_results():
    page = _NavPage()
    session = _session_with_page(page)
    # _ensure_page short-circuits because _page is set + not closed.
    out = await session.go_back()
    assert out == {"ok": True, "url": "http://example.com/prev", "title": "T"}
    out = await session.go_forward()
    assert out == {"ok": True, "url": "http://example.com/next", "title": "T"}
    out = await session.reload_page()
    assert out["ok"] is True
    assert page.ops == ["back", "forward", "reload"]


# ── url listener fan-out (main frame only) ────────────────────────────────


class _FrameStub:
    def __init__(self, url, parent=None):
        self.url = url
        self.parent_frame = parent


class _EventPage(_FakePage):
    def __init__(self):
        super().__init__()
        self._handlers = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, *args):
        for h in self._handlers.get(event, []):
            h(*args)


def test_url_listener_main_frame_only():
    page = _EventPage()
    session = _session_with_page(page)
    seen = []
    session.add_url_listener(seen.append)

    page.emit("framenavigated", _FrameStub("http://a/", parent=None))
    page.emit("framenavigated", _FrameStub("http://sub/", parent=object()))  # subframe ignored

    assert seen == ["http://a/"]


def test_url_listener_handler_installed_once_per_page():
    page = _EventPage()
    session = _session_with_page(page)
    seen = []
    session.add_url_listener(seen.append)
    session.add_url_listener(seen.append)  # same cb twice → registered once
    assert len(page._handlers["framenavigated"]) == 1

    page.emit("framenavigated", _FrameStub("http://a/", parent=None))
    assert seen == ["http://a/"]


# ── frame forwarder backpressure ──────────────────────────────────────────


async def test_frame_forwarder_drops_while_send_in_flight():
    release = asyncio.Event()
    sends = []

    async def slow_send(message):
        sends.append(message)
        await release.wait()

    fwd = _FrameForwarder(slow_send)
    assert fwd.offer("f1") is True   # scheduled
    await asyncio.sleep(0)           # let _send start and block on release
    assert fwd.offer("f2") is False  # dropped, send still in flight
    assert fwd.offer("f3") is False
    assert fwd.dropped == 2

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fwd.sent == 1
    # After the inflight send completes, a new frame is accepted again.
    assert fwd.offer("f4") is True


# ── WS auth gate + protocol round-trip ────────────────────────────────────


def _ws_app(ws_validate=None, ws_authorize=None):
    app = FastAPI()
    app.include_router(setup_browser_routes(ws_validate=ws_validate, ws_authorize=ws_authorize))
    return app


@pytest.fixture
def stub_live_session(monkeypatch):
    """Stub embedded_browser.session so the WS route streams one fake frame and
    records input without launching real Chromium."""
    captured = {"mouse": [], "frames_started": False}

    async def get_current_url():
        return {"url": "http://stub/", "title": "Stub"}

    async def start_screencast(on_frame):
        captured["frames_started"] = True
        # Emit a single frame immediately (sync callback, like real CDP).
        on_frame("ZmFrZQ==", {"deviceWidth": 800, "deviceHeight": 600})

    async def stop_screencast():
        captured["frames_started"] = False

    async def input_mouse(kind, x, y, **kw):
        captured["mouse"].append((kind, x, y, kw))

    fake = SimpleNamespace(
        get_current_url=get_current_url,
        start_screencast=start_screencast,
        stop_screencast=stop_screencast,
        input_mouse=input_mouse,
        add_url_listener=lambda cb: None,
        remove_url_listener=lambda cb: None,
    )
    monkeypatch.setattr(embedded_browser, "session", fake)
    # Reset the module-level single-viewer handle between tests.
    import routes.browser_routes as br

    monkeypatch.setattr(br, "_current_viewer", None, raising=False)
    return captured


def test_ws_rejects_invalid_session():
    app = _ws_app(ws_validate=lambda token: token == "good")
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/browser/ws"):
                pass  # no cookie → 1008 close


def test_ws_rejects_without_privilege(stub_live_session):
    app = _ws_app(
        ws_validate=lambda token: True,
        ws_authorize=lambda token: False,  # valid session, lacks can_use_browser
    )
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/browser/ws"):
                pass


def test_ws_streams_frame_and_receives_input(stub_live_session):
    # auth disabled (both validators default permissive)
    app = _ws_app()
    with TestClient(app) as c:
        with c.websocket_connect("/api/browser/ws") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "frame"
            assert frame["data"] == "ZmFrZQ=="
            assert frame["w"] == 800 and frame["h"] == 600

            ws.send_text(json.dumps({"type": "mouse", "kind": "click", "x": 12, "y": 34}))
            # Give the server loop a moment to dispatch.
            import time

            for _ in range(50):
                if stub_live_session["mouse"]:
                    break
                time.sleep(0.02)
    assert stub_live_session["mouse"], "mouse message never reached the stub"
    kind, x, y, kw = stub_live_session["mouse"][0]
    assert (kind, x, y) == ("click", 12, 34)


def test_ws_reports_browser_unavailable(monkeypatch):
    async def get_current_url():
        raise embedded_browser.BrowserUnavailable("no chromium")

    fake = SimpleNamespace(get_current_url=get_current_url)
    monkeypatch.setattr(embedded_browser, "session", fake)
    app = _ws_app()
    with TestClient(app) as c:
        with c.websocket_connect("/api/browser/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "no chromium" in msg["message"]
