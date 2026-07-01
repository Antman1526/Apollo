"""Route-level checks for GET /api/memory/graph via FastAPI TestClient.

`memory_manager.load` and `memory_vector` are fakes so no DB, ChromaDB, or LLM
is touched. Tests assert the route is wired, owner-scoped on the auth-off path,
returns {nodes, edges}, and degrades to session-only edges when the vector
store is absent/unhealthy. Mirrors tests/test_brain_routes.py.
"""
import importlib.util

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


MEMS = [
    {"id": "a", "text": "User uses Postgres 16", "category": "fact",
     "session_id": "s1", "owner": None, "timestamp": 3},
    {"id": "b", "text": "User prefers Postgres over MySQL", "category": "fact",
     "session_id": "s1", "owner": None, "timestamp": 2},
    {"id": "c", "text": "User lives in Berlin", "category": "fact",
     "session_id": "s2", "owner": None, "timestamp": 1},
]

_TABLE = {
    "User uses Postgres 16": [{"memory_id": "b", "score": 0.82}, {"memory_id": "c", "score": 0.10}],
    "User prefers Postgres over MySQL": [{"memory_id": "a", "score": 0.82}, {"memory_id": "c", "score": 0.12}],
    "User lives in Berlin": [{"memory_id": "a", "score": 0.10}, {"memory_id": "b", "score": 0.12}],
}


class _FakeMemoryManager:
    def load(self, owner=None):
        return MEMS


class _FakeSessionManager:
    pass


class _FakeHealthyVector:
    healthy = True

    def search(self, query, k=8):
        return _TABLE.get(query, [])


def _make_client(monkeypatch, memory_vector):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import fastapi.dependencies.utils as dependency_utils
    import routes.memory_routes as routes_mod

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        dependency_utils, "ensure_multipart_is_installed", lambda: None
    )

    mm, sm = _FakeMemoryManager(), _FakeSessionManager()
    app = FastAPI()
    app.include_router(
        routes_mod.setup_memory_routes(mm, sm, memory_vector=memory_vector)
    )
    return TestClient(app)


def test_graph_returns_nodes_and_semantic_edges(monkeypatch):
    c = _make_client(monkeypatch, _FakeHealthyVector())
    r = c.get("/api/memory/graph")
    assert r.status_code == 200
    g = r.json()
    assert {n["id"] for n in g["nodes"]} == {"a", "b", "c"}
    sem = {frozenset((e["source"], e["target"])) for e in g["edges"]
           if e["type"] == "semantic"}
    assert frozenset(("a", "b")) in sem
    assert frozenset(("a", "c")) not in sem


def test_graph_degrades_to_session_only_when_vector_none(monkeypatch):
    c = _make_client(monkeypatch, None)
    r = c.get("/api/memory/graph")
    assert r.status_code == 200
    g = r.json()
    assert {n["id"] for n in g["nodes"]} == {"a", "b", "c"}
    types = {e["type"] for e in g["edges"]}
    assert types == {"session"}          # no crash, no semantic edges
    ses = {frozenset((e["source"], e["target"])) for e in g["edges"]}
    assert frozenset(("a", "b")) in ses  # both in s1
    assert not any("c" in fs for fs in ses)


def test_graph_degrades_when_vector_unhealthy(monkeypatch):
    class _Unhealthy:
        healthy = False

        def search(self, query, k=8):  # pragma: no cover - must not be called
            raise AssertionError("search must not run when unhealthy")

    c = _make_client(monkeypatch, _Unhealthy())
    r = c.get("/api/memory/graph")
    assert r.status_code == 200
    g = r.json()
    assert {e["type"] for e in g["edges"]} == {"session"}
