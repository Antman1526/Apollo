"""Route-level checks for the local-models API via FastAPI TestClient.

Skipped unless FastAPI + SQLAlchemy are really installed (the import chain
pulls core.database). Runs in a full environment / venv, not the stubbed
bare-interpreter test run.
"""
import importlib.util

import pytest


def _has_real(mod: str) -> bool:
    # find_spec raises (not just returns None) when conftest has stubbed the
    # module as a MagicMock without a real __spec__ — treat any failure as
    # "not really installed".
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "bcrypt", "cryptography"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+bcrypt+cryptography installed"
)


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.localmodels_routes as routes_mod
    from services.localmodels.scanner import LocalModel

    catalog = [
        LocalModel("lm_a", "ModelA-Q4_K_M", "/m/a.gguf", "Q4_K_M", "chat", 4 * 1024**3, "/m"),
        LocalModel("lm_e", "nomic-embed", "/m/e.gguf", "F16", "embedding", 200 * 1024**2, "/m"),
    ]
    state = {"dirs": ["/m"], "running": set(), "started": [], "stopped": []}

    class _FakeServer:
        def status(self):
            return {mid: {"running": True} for mid in state["running"]}

        def ensure_running(self, ref):
            state["started"].append(ref)
            state["running"].add(ref)
            return "http://127.0.0.1:9001"

        def stop(self, model_id):
            if model_id in state["running"]:
                state["stopped"].append(model_id)
                state["running"].discard(model_id)
                return True
            return False

    monkeypatch.setattr(routes_mod, "require_admin", lambda request: None)
    monkeypatch.setattr(routes_mod, "scan_dirs", lambda dirs: catalog)
    monkeypatch.setattr(routes_mod, "get_local_model_dirs", lambda: state["dirs"])
    monkeypatch.setattr(routes_mod, "set_local_model_dirs",
                        lambda dirs: state.__setitem__("dirs", dirs) or dirs)
    monkeypatch.setattr(routes_mod, "get_server", lambda: _FakeServer())
    monkeypatch.setattr(routes_mod.lifecycle, "rescan", lambda: catalog)

    app = FastAPI()
    app.include_router(routes_mod.setup_localmodels_routes())
    return TestClient(app), state


def test_list_models(client):
    c, _ = client
    r = c.get("/api/local-models")
    assert r.status_code == 200
    body = r.json()
    assert body["dirs"] == ["/m"]
    names = {m["name"] for m in body["models"]}
    assert names == {"ModelA-Q4_K_M", "nomic-embed"}
    assert all("running" in m and "quant" in m and "kind" in m for m in body["models"])


def test_get_and_put_dirs(client):
    c, state = client
    assert c.get("/api/local-models/dirs").json() == {"dirs": ["/m"]}
    r = c.put("/api/local-models/dirs", json={"dirs": ["/x", "/y"]})
    assert r.status_code == 200
    assert r.json() == {"dirs": ["/x", "/y"]}
    assert state["dirs"] == ["/x", "/y"]


def test_scan(client):
    c, _ = client
    r = c.post("/api/local-models/scan")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_start_and_stop(client):
    c, state = client
    r = c.post("/api/local-models/lm_a/start")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "base_url": "http://127.0.0.1:9001"}
    assert "lm_a" in state["started"]
    r2 = c.post("/api/local-models/lm_a/stop")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert "lm_a" in state["stopped"]
