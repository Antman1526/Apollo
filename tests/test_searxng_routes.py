"""SearXNG sidecar status/install endpoints."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.search_routes import setup_search_routes


def _client():
    app = FastAPI()
    app.include_router(setup_search_routes(config={}))
    return TestClient(app)


def test_status_reports_runtime():
    with patch("services.searxng.runtime.get_runtime") as rt, \
         patch("routes.search_routes.require_admin", return_value=None):
        rt.return_value.status.return_value = "running"
        rt.return_value.url = "http://127.0.0.1:8893"
        res = _client().get("/api/search/searxng/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert body["url"] == "http://127.0.0.1:8893"
    assert body["installing"] is False


def test_install_kicks_off_script():
    started = {}

    def fake_thread(target=None, **kw):
        class T:
            def start(self_inner):
                started["yes"] = True
        return T()

    with patch("routes.search_routes.require_admin", return_value=None), \
         patch("routes.search_routes.threading.Thread", side_effect=fake_thread):
        res = _client().post("/api/search/searxng/install")
    assert res.status_code == 200
    assert res.json()["started"] is True
    assert started.get("yes") is True


def test_install_stops_sidecar_before_script():
    """stop() must be called inside _run(), before the install script runs."""
    captured = {}
    call_order = []

    def fake_thread(target=None, **kw):
        captured["target"] = target
        class T:
            def start(self_inner):
                pass
        return T()

    with patch("routes.search_routes.require_admin", return_value=None), \
         patch("routes.search_routes.threading.Thread", side_effect=fake_thread):
        _client().post("/api/search/searxng/install")

    assert "target" in captured, "Thread was not created"

    # Build a fake runtime that records call order
    from unittest.mock import MagicMock
    fake_rt = MagicMock()
    fake_rt.stop.side_effect = lambda: call_order.append("stop")
    fake_rt.start.side_effect = lambda: call_order.append("start")

    # Build a fake Popen whose stdout yields one line and exits 0
    class FakePopen:
        returncode = 0
        stdout = iter(["installing...\n"])
        def wait(self):
            call_order.append("script")
            return 0

    import subprocess as _sp
    with patch("routes.search_routes.subprocess.Popen", return_value=FakePopen()), \
         patch("services.searxng.runtime.get_runtime", return_value=fake_rt):
        captured["target"]()

    assert "stop" in call_order, "stop() was not called"
    assert "script" in call_order, "install script was not run"
    assert "start" in call_order, "start() was not called after script"
    # stop must precede script, script must precede start
    assert call_order.index("stop") < call_order.index("script"), \
        f"stop must come before script; got {call_order}"
    assert call_order.index("script") < call_order.index("start"), \
        f"script must come before start; got {call_order}"
