import pytest
from types import SimpleNamespace

from routes.browser_routes import NavigateRequest, ScriptRequest, setup_browser_routes
from services.browser import embedded_browser
from src import tool_implementations


def _route(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        state=SimpleNamespace(current_user="alice"),
    )


def _configured_request(user=None):
    auth_manager = SimpleNamespace(is_configured=True, get_privileges=lambda _user: {"can_use_browser": True})
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        state=SimpleNamespace(current_user=user),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )


def test_browser_url_normalization_and_blocking():
    assert embedded_browser.normalize_url("localhost:3000") == "http://localhost:3000"
    assert embedded_browser.normalize_url("example.com:3000/path") == "http://example.com:3000/path"
    assert embedded_browser.normalize_url("https://example.com/path") == "https://example.com/path"

    for url in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "javascript:80",
        "mailto:user@example.com",
        "data:text/html,hi",
        "chrome://version",
        "localhost:99999",
        "http://localhost:99999",
    ]:
        with pytest.raises(embedded_browser.BrowserSecurityError):
            embedded_browser.normalize_url(url)


def test_localhost_detection_finds_dev_server_urls():
    out = embedded_browser.detect_localhost_urls(
        "Vite ready at localhost:5173 and http://127.0.0.1:8000/docs and [::1]:9000. Ignore localhost:99999"
    )
    assert out == ["http://localhost:5173", "http://127.0.0.1:8000/docs", "http://[::1]:9000"]


def test_browser_events_have_monotonic_ids_after_deque_rollover():
    session = embedded_browser.EmbeddedBrowserSession()
    for idx in range(205):
        session._record("console", f"line {idx}", "http://localhost:3000")

    events = session.events()

    assert len(events) == 200
    assert events[0]["id"] == 6
    assert events[-1]["id"] == 205


def test_browser_script_results_are_truncated_recursively():
    nested = {
        "items": ["x" * 25_000, {"deep": {"deeper": {"deepest": {"too_deep": "hidden"}}}}],
        "many": list(range(55)),
    }

    out = embedded_browser._truncate_value(nested)

    assert out["items"][0].endswith("chars total)")
    assert out["items"][1]["deep"]["deeper"]["deepest"] == "... (truncated nested result)"
    assert out["many"][-1] == "... (truncated, 55 items total)"


@pytest.mark.asyncio
async def test_browser_route_honors_disabled_auth(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    router = setup_browser_routes()
    endpoint = _route(router, "/api/browser/status", "GET")

    out = await endpoint(request=_configured_request(user=None))

    assert "available" in out


@pytest.mark.asyncio
async def test_browser_route_maps_browser_input_errors_to_400(monkeypatch):
    async def fake_execute(_script):
        raise ValueError("script is required")

    monkeypatch.setattr(embedded_browser.session, "execute_script", fake_execute)

    router = setup_browser_routes()
    endpoint = _route(router, "/api/browser/execute", "POST")

    with pytest.raises(Exception) as raised:
        await endpoint(ScriptRequest(script="return 1"), request=_request())

    assert getattr(raised.value, "status_code", None) == 400
    assert raised.value.detail == "script is required"


@pytest.mark.asyncio
async def test_browser_route_delegates_navigation(monkeypatch):
    async def fake_navigate(url):
        return {"ok": True, "url": url, "title": "Local"}

    monkeypatch.setattr(embedded_browser.session, "navigate", fake_navigate)

    router = setup_browser_routes()
    endpoint = _route(router, "/api/browser/navigate", "POST")

    out = await endpoint(NavigateRequest(url="http://localhost:3000"), request=_request())

    assert out == {"ok": True, "url": "http://localhost:3000", "title": "Local"}


@pytest.mark.asyncio
async def test_browser_tool_dispatches_visible_text(monkeypatch):
    async def fake_text():
        return {"url": "http://localhost:3000", "text": "Rendered app"}

    monkeypatch.setattr(embedded_browser.session, "get_visible_text", fake_text)

    out = await tool_implementations.do_browser('{"action":"getVisibleText"}', owner="alice")

    assert out["exit_code"] == 0
    assert out["browser"]["text"] == "Rendered app"
