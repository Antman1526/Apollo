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
