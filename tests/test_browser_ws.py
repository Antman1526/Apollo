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


# ── agent-action listeners + take-over gate (co-pilot overlay) ────────────


class _ActionPage(_FakePage):
    """Page stub for click/type: exposes locator().first.bounding_box() and
    records page.click/fill calls. `fail` makes click raise."""

    def __init__(self, box=None, fail=None):
        super().__init__()
        self.url = "http://example.com/"
        self.box = box
        self.fail = fail
        self.calls = []

    def locator(self, selector):
        page = self

        class _First:
            async def bounding_box(self, **kw):
                return page.box

        class _Locator:
            first = _First()

        return _Locator()

    async def click(self, selector, **kw):
        if self.fail is not None:
            raise self.fail
        self.calls.append(("click", selector))

    async def fill(self, selector, text, **kw):
        self.calls.append(("fill", selector, text))


async def test_action_listener_fan_out_and_removal():
    page = _ActionPage(box={"x": 10, "y": 20, "width": 100, "height": 40})
    session = _session_with_page(page)
    seen = []

    def cb(event):
        seen.append(event)

    session.add_action_listener(cb)
    session.add_action_listener(cb)  # idempotent
    await session.click("button.login")

    assert [(e["action"], e["phase"]) for e in seen] == [("click", "start"), ("click", "done")]
    start = seen[0]
    assert (start["x"], start["y"]) == (60, 40)  # element centre in page px
    assert start["detail"] == "button.login"
    assert isinstance(start["ts"], float)
    assert page.calls == [("click", "button.login")]
    # Recorded into the console/event deque as well.
    assert any(e["kind"] == "agent" and "click start" in e["message"] for e in session.events())

    session.remove_action_listener(cb)
    session.remove_action_listener(cb)  # removing twice is harmless
    await session.click("a.next")
    assert len(seen) == 2


async def test_action_listener_exception_does_not_break_the_op():
    page = _ActionPage(box=None)  # no bounding box → coords None
    session = _session_with_page(page)
    seen = []

    def boom(event):
        raise RuntimeError("listener exploded")

    session.add_action_listener(boom)
    session.add_action_listener(seen.append)
    out = await session.click("#go")

    assert out["ok"] is True
    assert page.calls == [("click", "#go")]
    assert [e["phase"] for e in seen] == ["start", "done"]
    assert seen[0]["x"] is None and seen[0]["y"] is None


async def test_type_emits_elided_preview_and_error_phase_reraises():
    page = _ActionPage(box={"x": 0, "y": 0, "width": 10, "height": 10})
    session = _session_with_page(page)
    seen = []
    session.add_action_listener(seen.append)

    long_text = "x" * 60
    await session.type("#q", long_text)
    assert seen[0]["action"] == "type"
    assert seen[0]["detail"] == f'"{"x" * 39}…" into #q'
    assert (seen[0]["x"], seen[0]["y"]) == (5, 5)
    assert page.calls == [("fill", "#q", long_text)]

    failing = _ActionPage(box=None, fail=RuntimeError("no such element " + "z" * 300))
    session = _session_with_page(failing)
    errors = []
    session.add_action_listener(errors.append)
    with pytest.raises(RuntimeError):
        await session.click("#missing")
    assert [e["phase"] for e in errors] == ["start", "error"]
    assert errors[1]["detail"].startswith("no such element")
    assert len(errors[1]["detail"]) == 200


async def test_user_control_blocks_agent_ops_but_not_live_input():
    page = _ActionPage(box={"x": 0, "y": 0, "width": 2, "height": 2})
    session = _session_with_page(page)
    seen = []
    session.add_action_listener(seen.append)

    session.set_user_control(True)
    assert session.user_control is True
    with pytest.raises(embedded_browser.BrowserTakenOver) as raised:
        await session.click("button")
    assert "taken control" in str(raised.value)
    assert isinstance(raised.value, embedded_browser.BrowserUnavailable)
    assert page.calls == []
    assert seen == []  # nothing narrated for a refused op

    # Live-view input from the panel keeps flowing.
    await session.input_mouse("move", 1, 2)
    assert page.mouse.calls == [("move", 1, 2)]

    # Panel POST/WS handlers bypass the gate and are NOT narrated.
    out = await session.click("button", _from_user=True)
    assert out["ok"] is True and page.calls == [("click", "button")]
    assert seen == []

    session.set_user_control(False)
    await session.click("button")
    assert [e["phase"] for e in seen] == ["start", "done"]
    kinds = [e["kind"] for e in session.events()]
    assert kinds.count("control") == 2


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
    captured = {"mouse": [], "frames_started": False, "action_listeners": [], "control": []}

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
        # A "click" from the panel doubles as the trigger for a synthetic agent
        # action, so tests can fire the listener from inside the server loop.
        if kind == "agent-probe":
            for cb in list(captured["action_listeners"]):
                cb({"action": "click", "phase": "start", "detail": "#probe", "x": x, "y": y, "ts": 1.0})

    def set_user_control(flag):
        fake.user_control = bool(flag)
        captured["control"].append(fake.user_control)

    fake = SimpleNamespace(
        get_current_url=get_current_url,
        start_screencast=start_screencast,
        stop_screencast=stop_screencast,
        input_mouse=input_mouse,
        add_url_listener=lambda cb: None,
        remove_url_listener=lambda cb: None,
        add_action_listener=captured["action_listeners"].append,
        remove_action_listener=captured["action_listeners"].remove,
        set_user_control=set_user_control,
        user_control=False,
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


def _receive_type(ws, wanted: str, limit: int = 6):
    """The connect handshake sends a frame AND a control message; their order
    depends on task scheduling, so read until the wanted type shows up."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == wanted:
            return msg
    raise AssertionError(f"no {wanted!r} message within {limit} messages")


def test_ws_streams_frame_and_receives_input(stub_live_session):
    # auth disabled (both validators default permissive)
    app = _ws_app()
    with TestClient(app) as c:
        with c.websocket_connect("/api/browser/ws") as ws:
            frame = _receive_type(ws, "frame")
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


def test_ws_sends_control_mode_on_connect_and_round_trips_takeover(stub_live_session):
    app = _ws_app()
    with TestClient(app) as c:
        with c.websocket_connect("/api/browser/ws") as ws:
            hello = _receive_type(ws, "control")
            assert hello == {"type": "control", "mode": "agent"}

            ws.send_text(json.dumps({"type": "control", "mode": "user"}))
            assert _receive_type(ws, "control") == {"type": "control", "mode": "user"}
            ws.send_text(json.dumps({"type": "control", "mode": "agent"}))
            assert _receive_type(ws, "control") == {"type": "control", "mode": "agent"}
    assert stub_live_session["control"] == [True, False]
    # The viewer unregistered its action listener on disconnect.
    assert stub_live_session["action_listeners"] == []


def test_ws_forwards_agent_action_to_viewer(stub_live_session):
    app = _ws_app()
    with TestClient(app) as c:
        with c.websocket_connect("/api/browser/ws") as ws:
            _receive_type(ws, "control")  # handshake done → listener registered
            assert len(stub_live_session["action_listeners"]) == 1
            ws.send_text(json.dumps({"type": "mouse", "kind": "agent-probe", "x": 40, "y": 50}))
            msg = _receive_type(ws, "agent_action")
            assert msg["action"] == "click" and msg["phase"] == "start"
            assert (msg["x"], msg["y"]) == (40, 50)
            assert msg["detail"] == "#probe"


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
