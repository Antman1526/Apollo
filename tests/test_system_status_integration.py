import asyncio
from types import SimpleNamespace

from routes.system_status_routes import setup_system_status_routes
from services.system_status import build_system_status


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


class FakeMemory:
    def load_all(self):
        return [{"id": "m1", "text": "one"}, {"id": "m2", "text": "two"}]


class FakeVector:
    healthy = True

    def count(self):
        return 2


class FakeMcp:
    def get_all_statuses(self):
        return {
            "one": {"status": "connected"},
            "two": {"status": "error"},
            "three": {"status": "disconnected"},
        }

    def get_all_tools(self):
        return [{"name": "search"}, {"name": "read"}]


def _ready_report():
    return {
        "ready": True,
        "version": "test",
        "checks": {
            "database": {"ok": True},
            "data_dir": {"ok": True, "path": "/tmp/test"},
            "local_first": {"ok": True, "local": True},
        },
    }


def test_build_system_status_reports_core_subsystems(monkeypatch):
    monkeypatch.setattr("services.system_status.terminal_status", lambda: {
        "label": "Terminal",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {},
    })

    out = build_system_status(
        memory_manager=FakeMemory(),
        memory_vector=FakeVector(),
        mcp_manager=FakeMcp(),
        task_scheduler=SimpleNamespace(_running=True, _task=None, _executing={"task-1"}, _concurrency_cap=1),
        readiness_provider=_ready_report,
    )

    assert out["total"] == 5
    assert out["ready_count"] == 4
    assert out["ok"] is False
    assert out["components"]["storage"]["ready"] is True
    assert out["components"]["memory"]["metrics"]["entries"] == 2
    assert out["components"]["memory"]["metrics"]["vector_entries"] == 2
    assert out["components"]["tool_servers"]["state"] == "degraded"
    assert out["components"]["tool_servers"]["metrics"]["errors"] == 1
    assert out["components"]["background"]["metrics"]["executing"] == 1


def test_build_system_status_degrades_when_memory_vector_missing(monkeypatch):
    monkeypatch.setattr("services.system_status.terminal_status", lambda: {
        "label": "Terminal",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {},
    })

    out = build_system_status(
        memory_manager=FakeMemory(),
        memory_vector=None,
        mcp_manager=None,
        task_scheduler=SimpleNamespace(_running=True, _task=None, _executing=set(), _concurrency_cap=1),
        readiness_provider=_ready_report,
    )

    memory = out["components"]["memory"]
    assert memory["ready"] is False
    assert memory["state"] == "degraded"
    assert memory["metrics"]["entries"] == 2
    assert memory["metrics"]["vector_attached"] is False


def test_system_status_route_is_admin_gated(monkeypatch):
    monkeypatch.setattr("routes.system_status_routes.build_system_status", lambda **kwargs: {"ok": True, "components": {}})
    router = setup_system_status_routes()
    target = _route(router, "/api/system/status", "GET")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    out = asyncio.run(target(request=request))

    assert out == {"ok": True, "components": {}}
