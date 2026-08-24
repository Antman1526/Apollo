"""Route-level checks for /api/activity and /api/tasks/assign."""
import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


def _evict_module_stubs():
    """Runtime cleanup of MagicMock module stubs left by other suites.

    Collection-time imports mean the pollution (e.g. test_unknown_tool_calls
    mocking sqlalchemy/core.database in sys.modules) lands before tests run,
    so this must be called inside each test/fixture, not at module import.
    """
    for mod in list(sys.modules):
        if (mod in ("core.database", "routes.activity_routes", "routes.task_routes",
                    "services.activity_ledger")
                or mod.startswith("sqlalchemy")) and isinstance(sys.modules[mod], MagicMock):
            sys.modules.pop(mod)


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "bcrypt", "cryptography"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+bcrypt+cryptography installed"
)


@pytest.fixture
def activity_client(monkeypatch):
    _evict_module_stubs()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.activity_routes as routes_mod

    events = [
        {"id": "e1", "tool": "bash", "input_preview": "ls", "undoable": False, "undone": False},
        {"id": "e2", "tool": "write_file", "path": "/tmp/x", "undoable": True, "undone": False},
    ]
    state = {"undone": []}
    monkeypatch.setattr(routes_mod, "require_admin", lambda r: None)
    monkeypatch.setattr(
        routes_mod, "list_events",
        lambda q=None, tool=None, session_id=None, limit=100, offset=0: [
            e for e in events if (not tool or e["tool"] == tool)
        ],
    )
    def fake_undo(eid):
        if eid == "e2":
            state["undone"].append(eid)
            return {"ok": True, "action": "restored"}
        return {"ok": False, "error": "event is not undoable"}
    monkeypatch.setattr(routes_mod, "undo_event", fake_undo)

    app = FastAPI()
    app.include_router(routes_mod.setup_activity_routes())
    return TestClient(app), state


def test_list_activity(activity_client):
    c, _ = activity_client
    r = c.get("/api/activity")
    assert r.status_code == 200
    assert r.json()["count"] == 2
    r2 = c.get("/api/activity", params={"tool": "bash"})
    assert [e["tool"] for e in r2.json()["events"]] == ["bash"]


def test_undo_route(activity_client):
    c, state = activity_client
    assert c.post("/api/activity/e2/undo").status_code == 200
    assert state["undone"] == ["e2"]
    r = c.post("/api/activity/e1/undo")
    assert r.status_code == 400
    assert "not undoable" in r.json()["error"]


def test_assign_creates_and_runs_task(monkeypatch):
    _evict_module_stubs()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.database import Base
    import routes.task_routes as routes_mod

    from sqlalchemy.pool import StaticPool
    # StaticPool: TestClient serves routes on another thread; a plain
    # :memory: engine would hand that thread a fresh empty database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(routes_mod, "SessionLocal", TestSessionLocal)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    ran = []

    class FakeScheduler:
        async def run_task_now(self, task_id, force=False):
            ran.append(task_id)
            return True

    app = FastAPI()
    app.include_router(routes_mod.setup_task_routes(FakeScheduler()))
    c = TestClient(app)

    # Explicit name so the LLM-backed name generator is never invoked.
    r = c.post("/api/tasks/assign", json={"prompt": "Summarize my notes", "name": "Note summary"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["started"]
    assert body["task"]["task_type"] == "llm"
    assert body["task"]["trigger_type"] == "webhook"
    assert ran == [body["task"]["id"]]

    r2 = c.post("/api/tasks/assign", json={"prompt": "   "})
    assert r2.status_code == 400
