"""Model hub: free-model filtering, GGUF pull safety, download flow, routes."""
import importlib.util
import sys
import time
from unittest.mock import MagicMock

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "httpx"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+httpx installed"
)


def _evict_module_stubs():
    for mod in list(sys.modules):
        if (mod in ("core.database", "services.model_hub", "routes.hub_routes")
                or mod.startswith("sqlalchemy")) and isinstance(sys.modules[mod], MagicMock):
            sys.modules.pop(mod)


def test_is_free_filter():
    _evict_module_stubs()
    from services.model_hub import _is_free
    assert _is_free({"pricing": {"prompt": "0", "completion": "0"}})
    assert not _is_free({"pricing": {"prompt": "0.000001", "completion": "0"}})
    assert not _is_free({"pricing": {"prompt": "bogus"}})
    # No pricing metadata (OpenCode Zen): fall back to the id/name marker.
    assert _is_free({"id": "deepseek-v4-flash-free"})
    assert not _is_free({"id": "claude-opus"})


def test_gguf_input_validation(tmp_path):
    _evict_module_stubs()
    from services.model_hub import start_gguf_download
    ok_dir = str(tmp_path)
    assert not start_gguf_download("no-slash", "m.gguf", ok_dir)["ok"]
    assert not start_gguf_download("a/b; rm -rf /", "m.gguf", ok_dir)["ok"]
    assert not start_gguf_download("org/repo", "../../etc/passwd", ok_dir)["ok"]
    assert not start_gguf_download("org/repo", "model.bin", ok_dir)["ok"]
    assert not start_gguf_download("org/repo", "m.gguf", str(tmp_path / "missing"))["ok"]


def test_gguf_download_streams_and_renames(tmp_path, monkeypatch):
    _evict_module_stubs()
    import services.model_hub as hub

    class _FakeResp:
        headers = {"content-length": "10"}
        def raise_for_status(self): pass
        def iter_bytes(self, chunk_size=0):
            yield b"12345"
            yield b"67890"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import httpx
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeResp())
    rescanned = []
    monkeypatch.setattr("services.localmodels.lifecycle.rescan",
                        lambda: rescanned.append(True))

    res = hub.start_gguf_download("org/repo", "sub/model.gguf", str(tmp_path))
    assert res["ok"]
    deadline = time.time() + 5
    while time.time() < deadline:
        st = hub.download_status()
        row = next(d for d in st if d["repo_id"] == "org/repo")
        if row["status"] != "downloading":
            break
        time.sleep(0.05)
    assert row["status"] == "done"
    dest = tmp_path / "model.gguf"          # basename only — no subdir escape
    assert dest.read_bytes() == b"1234567890"
    assert not (tmp_path / "model.gguf.part").exists()
    assert rescanned


def test_codex_router_status_shape(monkeypatch):
    _evict_module_stubs()
    import services.model_hub as hub
    import socket

    def refuse(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(socket, "create_connection", refuse)
    st = hub.codex_router_status()
    assert st["running"] is False
    assert st["port"] == 4202
    assert "install_commands" in st and st["install_commands"]
    assert "note" in st


def test_free_endpoint_route_creates_and_pins_models(monkeypatch):
    _evict_module_stubs()
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.database import Base, ModelEndpoint
    import routes.hub_routes as routes_mod

    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(routes_mod, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(routes_mod, "require_admin", lambda r: None)
    monkeypatch.setattr(routes_mod, "list_free_models",
                        lambda p: [{"id": "m1:free"}, {"id": "m2:free"}])

    app = FastAPI()
    app.include_router(routes_mod.setup_hub_routes())
    c = TestClient(app)

    r = c.post("/api/hub/free-endpoint",
               json={"provider": "openrouter", "api_key": "sk-x"})
    assert r.status_code == 200
    body = r.json()
    assert body["free_models"] == 2

    db = TestSessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == body["endpoint_id"]).one()
        assert ep.base_url == "https://openrouter.ai/api/v1"
        assert json.loads(ep.cached_models) == ["m1:free", "m2:free"]
    finally:
        db.close()

    # Idempotent: same provider updates the row instead of duplicating.
    r2 = c.post("/api/hub/free-endpoint",
                json={"provider": "openrouter", "api_key": "sk-y"})
    assert r2.json()["endpoint_id"] == body["endpoint_id"]

    assert c.post("/api/hub/free-endpoint",
                  json={"provider": "nope", "api_key": "k"}).status_code == 400
    assert c.post("/api/hub/free-endpoint",
                  json={"provider": "opencode", "api_key": "  "}).status_code == 400


def test_gguf_download_route_requires_existing_dir(monkeypatch):
    _evict_module_stubs()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.hub_routes as routes_mod

    monkeypatch.setattr(routes_mod, "require_admin", lambda r: None)
    monkeypatch.setattr("services.localmodels.config.get_local_model_dirs",
                        lambda: ["/definitely/not/a/dir"])
    app = FastAPI()
    app.include_router(routes_mod.setup_hub_routes())
    c = TestClient(app)
    r = c.post("/api/hub/gguf-download",
               json={"repo_id": "org/repo", "file": "m.gguf"})
    assert r.status_code == 400
    assert "local model directory" in r.json()["detail"].lower()
