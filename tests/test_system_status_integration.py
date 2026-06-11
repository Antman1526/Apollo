import asyncio
from types import SimpleNamespace

from routes.system_status_routes import setup_system_status_routes
from services.system_status import auth_status, build_system_status, search_status


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

    def rebuild(self, memories):
        self.rebuilt = list(memories)


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
    for name in ("email_status", "documents_status", "model_endpoint_status"):
        monkeypatch.setattr("services.system_status." + name, lambda name=name: {
            "label": name,
            "ready": True,
            "state": "ready",
            "summary": "ok",
            "metrics": {},
        })
    monkeypatch.setattr("services.system_status.background_status", lambda task_scheduler=None: {
        "label": "Background Work",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {"executing": len(getattr(task_scheduler, "_executing", set()) or set())},
    })

    out = build_system_status(
        memory_manager=FakeMemory(),
        memory_vector=FakeVector(),
        mcp_manager=FakeMcp(),
        task_scheduler=SimpleNamespace(_running=True, _task=None, _executing={"task-1"}, _concurrency_cap=1),
        auth_manager=SimpleNamespace(users={"admin": {"is_admin": True}}, is_configured=True, signup_enabled=False, _sessions={}),
        rag_manager=SimpleNamespace(get_stats=lambda: {"chunks": 2}),
        personal_docs_mgr=SimpleNamespace(get_stats=lambda: {"total_documents": 1}),
        readiness_provider=_ready_report,
    )

    assert out["total"] == 10
    assert out["ready_count"] == 9
    assert out["ok"] is False
    assert out["components"]["storage"]["ready"] is True
    assert out["components"]["auth"]["ready"] is True
    assert out["components"]["memory"]["metrics"]["entries"] == 2
    assert out["components"]["memory"]["metrics"]["vector_entries"] == 2
    assert out["components"]["memory"].get("actions") in (None, [])
    assert out["components"]["search"]["ready"] is True
    assert out["components"]["tool_servers"]["state"] == "degraded"
    assert out["components"]["tool_servers"]["metrics"]["errors"] == 1
    assert out["components"]["tool_servers"]["actions"][0]["id"] == "tool_servers.reconnect_failed"
    assert out["components"]["background"]["metrics"]["executing"] == 1


def test_build_system_status_degrades_when_memory_vector_missing(monkeypatch):
    monkeypatch.setattr("services.system_status.terminal_status", lambda: {
        "label": "Terminal",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {},
    })
    for name in ("email_status", "documents_status", "model_endpoint_status"):
        monkeypatch.setattr("services.system_status." + name, lambda name=name: {
            "label": name,
            "ready": True,
            "state": "ready",
            "summary": "ok",
            "metrics": {},
        })
    monkeypatch.setattr("services.system_status.background_status", lambda task_scheduler=None: {
        "label": "Background Work",
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
        auth_manager=SimpleNamespace(users={}, is_configured=False, signup_enabled=False, _sessions={}),
        rag_manager=SimpleNamespace(get_stats=lambda: {}),
        personal_docs_mgr=SimpleNamespace(get_stats=lambda: {}),
        readiness_provider=_ready_report,
    )

    memory = out["components"]["memory"]
    assert memory["ready"] is False
    assert memory["state"] == "degraded"
    assert memory["metrics"]["entries"] == 2
    assert memory["metrics"]["vector_attached"] is False


def test_memory_status_degrades_on_vector_drift(monkeypatch):
    monkeypatch.setattr("services.system_status.terminal_status", lambda: {
        "label": "Terminal",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {},
    })
    for name in ("email_status", "documents_status", "model_endpoint_status"):
        monkeypatch.setattr("services.system_status." + name, lambda name=name: {
            "label": name,
            "ready": True,
            "state": "ready",
            "summary": "ok",
            "metrics": {},
        })
    monkeypatch.setattr("services.system_status.background_status", lambda task_scheduler=None: {
        "label": "Background Work",
        "ready": True,
        "state": "ready",
        "summary": "ok",
        "metrics": {},
    })

    class DriftedVector(FakeVector):
        def count(self):
            return 1

    out = build_system_status(
        memory_manager=FakeMemory(),
        memory_vector=DriftedVector(),
        mcp_manager=None,
        task_scheduler=SimpleNamespace(_running=True, _task=None, _executing=set(), _concurrency_cap=1),
        auth_manager=SimpleNamespace(users={}, is_configured=False, signup_enabled=False, _sessions={}),
        rag_manager=SimpleNamespace(get_stats=lambda: {}),
        personal_docs_mgr=SimpleNamespace(get_stats=lambda: {}),
        readiness_provider=_ready_report,
    )

    memory = out["components"]["memory"]
    assert memory["ready"] is False
    assert memory["state"] == "degraded"
    assert memory["metrics"]["indexable_entries"] == 2
    assert memory["metrics"]["vector_drift"] == 1
    assert memory["actions"][0]["id"] == "memory.rebuild_semantic_index"


def test_auth_status_blocks_configured_auth_without_admin():
    out = auth_status(SimpleNamespace(
        users={"alice": {"is_admin": False}},
        is_configured=True,
        signup_enabled=True,
        _sessions={"token": {"username": "alice"}},
    ))

    assert out["ready"] is False
    assert out["state"] == "blocked"
    assert out["metrics"]["users"] == 1
    assert out["metrics"]["admins"] == 0
    assert out["metrics"]["active_sessions"] == 1


def test_search_status_reports_partial_index_failure():
    out = search_status(
        rag_manager=SimpleNamespace(get_stats=lambda: {"chunks": 4}),
        personal_docs_mgr=SimpleNamespace(get_stats=lambda: (_ for _ in ()).throw(RuntimeError("bad index"))),
    )

    assert out["ready"] is False
    assert out["state"] == "degraded"
    assert out["metrics"]["global_index"]["available"] is True
    assert out["metrics"]["personal_docs"]["available"] is False


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


def test_system_status_route_uses_short_ttl_cache(monkeypatch):
    calls = []

    def fake_build(**_kwargs):
        calls.append(len(calls) + 1)
        return {"ok": True, "components": {}, "call": calls[-1]}

    monkeypatch.setattr("routes.system_status_routes.build_system_status", fake_build)
    router = setup_system_status_routes(status_cache_ttl=60)
    target = _route(router, "/api/system/status", "GET")
    request = SimpleNamespace(
        headers={},
        query_params={},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    first = asyncio.run(target(request=request))
    second = asyncio.run(target(request=request))

    assert first == second
    assert calls == [1]


def test_system_status_route_fresh_bypasses_cache(monkeypatch):
    calls = []

    def fake_build(**_kwargs):
        calls.append(len(calls) + 1)
        return {"ok": True, "components": {}, "call": calls[-1]}

    monkeypatch.setattr("routes.system_status_routes.build_system_status", fake_build)
    router = setup_system_status_routes(status_cache_ttl=60)
    target = _route(router, "/api/system/status", "GET")
    request = SimpleNamespace(
        headers={},
        query_params={"fresh": "true"},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    first = asyncio.run(target(request=request))
    second = asyncio.run(target(request=request))

    assert first["call"] == 1
    assert second["call"] == 2
    assert calls == [1, 2]


def test_system_action_rebuilds_memory_index(monkeypatch):
    vector = FakeVector()
    router = setup_system_status_routes(memory_manager=FakeMemory(), memory_vector=vector)
    target = _route(router, "/api/system/actions/{action_id}", "POST")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    out = asyncio.run(target(action_id="memory.rebuild_semantic_index", request=request))

    assert out["ok"] is True
    assert out["action_id"] == "memory.rebuild_semantic_index"
    assert out["rebuilt_entries"] == 2
    assert vector.rebuilt == [{"id": "m1", "text": "one"}, {"id": "m2", "text": "two"}]


def test_system_action_reconnects_failed_tool_servers(monkeypatch):
    class EnabledExpr:
        def __eq__(self, _other):
            return True

    class FakeServer:
        is_enabled = EnabledExpr()

        def __init__(self, server_id, name, status):
            self.id = server_id
            self.name = name
            self.status = status
            self.transport = "stdio"
            self.command = "cmd"
            self.args = '["--flag"]'
            self.env = '{"A":"B"}'
            self.url = None

    servers = [
        FakeServer("ok", "Ready", "connected"),
        FakeServer("bad", "Broken", "error"),
    ]

    class FakeQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return servers

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

        def close(self):
            self.closed = True

    class FakeMcpForReconnect:
        def __init__(self):
            self.disconnected = []
            self.connected = []

        def get_all_statuses(self):
            return {srv.id: {"status": srv.status} for srv in servers}

        async def disconnect_server(self, server_id):
            self.disconnected.append(server_id)

        async def connect_server(self, **kwargs):
            self.connected.append(kwargs)
            return True

        def get_server_status(self, server_id):
            return {"status": "connected", "tool_count": 3, "error": None}

    import core.database as database

    monkeypatch.setattr(database, "McpServer", FakeServer)
    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDb())

    mcp = FakeMcpForReconnect()
    router = setup_system_status_routes(mcp_manager=mcp)
    target = _route(router, "/api/system/actions/{action_id}", "POST")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    out = asyncio.run(target(action_id="tool_servers.reconnect_failed", request=request))

    assert out["ok"] is True
    assert out["attempted"] == 1
    assert out["reconnected"] == 1
    assert mcp.disconnected == ["bad"]
    assert mcp.connected[0]["server_id"] == "bad"
    assert mcp.connected[0]["args"] == ["--flag"]
    assert mcp.connected[0]["env"] == {"A": "B"}


def test_system_action_reconnects_missing_enabled_tool_servers(monkeypatch):
    class EnabledExpr:
        def __eq__(self, _other):
            return True

    class FakeServer:
        is_enabled = EnabledExpr()

        def __init__(self, server_id, name):
            self.id = server_id
            self.name = name
            self.transport = "stdio"
            self.command = "cmd"
            self.args = "[]"
            self.env = "{}"
            self.url = None

    servers = [
        FakeServer("ok", "Ready"),
        FakeServer("missing", "Missing"),
    ]

    class FakeQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return servers

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

        def close(self):
            self.closed = True

    class FakeMcpForReconnect:
        def __init__(self):
            self.disconnected = []
            self.connected = []

        def get_all_statuses(self):
            return {"ok": {"status": "connected"}}

        async def disconnect_server(self, server_id):
            self.disconnected.append(server_id)

        async def connect_server(self, **kwargs):
            self.connected.append(kwargs)
            return True

        def get_server_status(self, server_id):
            return {"status": "connected", "tool_count": 1, "error": None}

    import core.database as database

    monkeypatch.setattr(database, "McpServer", FakeServer)
    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDb())

    mcp = FakeMcpForReconnect()
    router = setup_system_status_routes(mcp_manager=mcp)
    target = _route(router, "/api/system/actions/{action_id}", "POST")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user="internal-tool"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    out = asyncio.run(target(action_id="tool_servers.reconnect_failed", request=request))

    assert out["ok"] is True
    assert out["attempted"] == 1
    assert out["reconnected"] == 1
    assert mcp.disconnected == ["missing"]
    assert [call["server_id"] for call in mcp.connected] == ["missing"]
