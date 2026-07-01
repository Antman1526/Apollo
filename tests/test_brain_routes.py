"""Route-level checks for the second-brain routes via FastAPI TestClient.

`brain.distill_session` / `brain.import_conversations` are monkeypatched so no
DB, session, or LLM is touched — the tests only assert the routes are wired,
owner-gated on the auth-off path (get_current_user -> None), and forward the
right arguments to the orchestrator. Mirrors tests/test_skill_pack_routes.py.
"""
import importlib.util
import io
import json

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "pydantic"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi + pydantic installed"
)


class _FakeMemoryManager:
    pass


class _FakeSessionManager:
    pass


class _FakeVector:
    healthy = True


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import fastapi.dependencies.utils as dependency_utils
    import routes.memory_routes as routes_mod

    # Operator-disabled auth: the documented single-user path. require_privilege
    # -> require_user returns "" so _owner is None and the routes are reachable
    # without a middleware-populated request.state.current_user.
    monkeypatch.setenv("AUTH_ENABLED", "false")

    # UploadFile route needs python-multipart; the shim mirrors test_app.py so
    # the suite runs even where the optional dep isn't installed.
    monkeypatch.setattr(
        dependency_utils, "ensure_multipart_is_installed", lambda: None
    )

    calls = {}

    def fake_distill_session(session_id, owner=None, memory_manager=None,
                             memory_vector=None):
        calls["distill"] = {
            "session_id": session_id, "owner": owner,
            "memory_manager": memory_manager, "memory_vector": memory_vector,
        }
        return {"added": 2, "skipped": 1}

    def fake_import_conversations(conversations, owner=None, memory_manager=None,
                                  memory_vector=None, llm_caller=None,
                                  source="import"):
        calls["import"] = {
            "conversations": conversations, "owner": owner,
            "memory_manager": memory_manager, "memory_vector": memory_vector,
            "source": source, "llm_caller": llm_caller,
        }
        return {"added": 3, "skipped": 0, "conversations": len(conversations)}

    monkeypatch.setattr(routes_mod.brain, "distill_session", fake_distill_session)
    monkeypatch.setattr(routes_mod.brain, "import_conversations",
                        fake_import_conversations)
    # Imported convos resolve a utility endpoint; stub so no real config needed.
    monkeypatch.setattr(routes_mod, "resolve_endpoint",
                        lambda role, owner=None: ("http://llm", "m", {}))

    mm, sm, vec = _FakeMemoryManager(), _FakeSessionManager(), _FakeVector()
    app = FastAPI()
    app.include_router(
        routes_mod.setup_memory_routes(mm, sm, memory_vector=vec)
    )
    # No auth middleware -> request.state.current_user unset -> owner is None
    # -> require_privilege returns "" (auth off), so routes are reachable.
    return TestClient(app), calls, mm, vec


def test_distill_session_forwards_args_and_returns_counts(client):
    c, calls, mm, vec = client
    r = c.post("/api/memory/distill-session", data={"session_id": "sess-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["added"] == 2 and body["skipped"] == 1
    d = calls["distill"]
    assert d["session_id"] == "sess-1"
    assert d["owner"] is None          # auth-off path
    assert d["memory_manager"] is mm
    assert d["memory_vector"] is vec


def test_distill_session_requires_session_id(client):
    c, _calls, _mm, _vec = client
    r = c.post("/api/memory/distill-session", data={})
    assert r.status_code == 422    # missing required Form field


def test_import_chat_export_parses_and_forwards(client, monkeypatch):
    c, calls, mm, vec = client
    import routes.memory_routes as routes_mod

    # parse_export turns the raw JSON into the conversation list the
    # orchestrator expects; stub it so the route contract is what's tested.
    monkeypatch.setattr(
        routes_mod, "parse_export",
        lambda obj: [{"title": "T", "messages": [{"role": "user", "text": "hi"}]}],
    )

    payload = json.dumps({"conversations": []}).encode("utf-8")
    r = c.post(
        "/api/memory/import-chat-export",
        files={"file": ("export.json", io.BytesIO(payload), "application/json")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["added"] == 3
    imp = calls["import"]
    assert imp["owner"] is None
    assert imp["source"] == "import"
    assert imp["memory_manager"] is mm
    assert imp["memory_vector"] is vec
    assert len(imp["conversations"]) == 1
    assert callable(imp["llm_caller"])


def test_import_chat_export_rejects_bad_json(client):
    c, _calls, _mm, _vec = client
    r = c.post(
        "/api/memory/import-chat-export",
        files={"file": ("x.json", io.BytesIO(b"not json"), "application/json")},
    )
    assert r.status_code == 400


def test_import_chat_export_empty_conversations_ok(client, monkeypatch):
    c, calls, _mm, _vec = client
    import routes.memory_routes as routes_mod
    monkeypatch.setattr(routes_mod, "parse_export", lambda obj: [])

    payload = json.dumps({"nonsense": 1}).encode("utf-8")
    r = c.post(
        "/api/memory/import-chat-export",
        files={"file": ("x.json", io.BytesIO(payload), "application/json")},
    )
    assert r.status_code == 200
    assert r.json()["conversations"] == 0
    assert "import" not in calls    # orchestrator not called on empty parse
