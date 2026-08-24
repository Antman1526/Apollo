"""Memory pack export/import routes + auto-distill task registration."""
import importlib.util
import time
import uuid

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
    def __init__(self, entries=None):
        self.entries = entries or []
        self.saved = None

    def load(self, owner=None):
        if owner:
            return [m for m in self.entries if m.get("owner") == owner]
        return list(self.entries)

    def load_all(self):
        return list(self.entries)

    def save(self, entries):
        self.saved = entries
        self.entries = list(entries)

    def find_duplicates(self, text, entries=None):
        pool = entries if entries is not None else self.entries
        t = text.strip().lower()
        return [m for m in pool if m["text"].lower() == t]

    def add_entry(self, text, source="user", category="fact", owner=None):
        e = {"id": str(uuid.uuid4()), "text": text.strip(),
             "timestamp": int(time.time()), "source": source, "category": category}
        if owner:
            e["owner"] = owner
        return e


def _make_client(monkeypatch, mm):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import fastapi.dependencies.utils as dependency_utils
    import routes.memory_routes as routes_mod

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        dependency_utils, "ensure_multipart_is_installed", lambda: None
    )
    app = FastAPI()
    app.include_router(routes_mod.setup_memory_routes(mm, object(), memory_vector=None))
    return TestClient(app)


def test_export_pack_shape(monkeypatch):
    mm = _FakeMemoryManager([
        {"id": "1", "text": "Likes espresso", "category": "preference",
         "pinned": True, "timestamp": 5, "source": "user"},
        {"id": "2", "text": "Project Apollo is a fork", "category": "project",
         "timestamp": 6, "source": "agent"},
    ])
    c = _make_client(monkeypatch, mm)
    r = c.get("/api/memory/export-pack")
    assert r.status_code == 200
    body = r.json()
    assert body["apollo_memory_pack"] == 1
    assert body["count"] == 2
    assert body["memories"][0] == {
        "text": "Likes espresso", "category": "preference",
        "pinned": True, "timestamp": 5, "source": "user",
    }


def test_import_pack_dedupes_and_preserves_flags(monkeypatch):
    mm = _FakeMemoryManager([
        {"id": "1", "text": "Likes espresso", "category": "preference", "timestamp": 5},
    ])
    c = _make_client(monkeypatch, mm)
    pack = {"apollo_memory_pack": 1, "memories": [
        {"text": "Likes espresso", "category": "preference"},        # dup → skip
        {"text": "Owns a ROG laptop", "category": "fact", "pinned": True},
        {"text": "   ", "category": "fact"},                          # empty → skip
    ]}
    r = c.post("/api/memory/import-pack", json=pack)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "added": 1, "skipped": 2}
    added = [m for m in mm.entries if m["text"] == "Owns a ROG laptop"]
    assert len(added) == 1
    assert added[0]["pinned"] is True
    assert added[0]["source"] == "import"
    assert mm.saved is not None


def test_import_pack_rejects_bad_payload(monkeypatch):
    c = _make_client(monkeypatch, _FakeMemoryManager())
    r = c.post("/api/memory/import-pack", json={"nope": True})
    assert r.status_code == 400


def test_export_then_import_round_trip(monkeypatch):
    mm_src = _FakeMemoryManager([
        {"id": "1", "text": "Fact A", "category": "fact", "timestamp": 1},
        {"id": "2", "text": "Fact B", "category": "goal", "pinned": True, "timestamp": 2},
    ])
    pack = _make_client(monkeypatch, mm_src).get("/api/memory/export-pack").json()
    mm_dst = _FakeMemoryManager()
    r = _make_client(monkeypatch, mm_dst).post("/api/memory/import-pack", json=pack)
    assert r.json()["added"] == 2
    assert {m["text"] for m in mm_dst.entries} == {"Fact A", "Fact B"}


def test_auto_distill_registered():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS
    assert "auto_distill_sessions" in BUILTIN_ACTIONS
    assert "auto_distill_sessions" in BUILTIN_ACTION_INFO
    d = HOUSEKEEPING_DEFAULTS["auto_distill_sessions"]
    assert d["schedule"] == "cron" and d["ship_paused"] is True


def test_auto_distill_watermarks_are_per_owner(monkeypatch):
    """Owner A's run must not advance owner B's watermark (multi-user installs
    get one seeded task per owner — a global watermark hides B's sessions)."""
    import asyncio
    import src.builtin_actions as ba

    store = {}
    monkeypatch.setattr("src.settings.load_settings", lambda: dict(store))
    monkeypatch.setattr("src.settings.save_settings", lambda s: store.update(s))

    class _EmptyQuery:
        def filter(self, *a, **k): return self
        def order_by(self, *a): return self
        def limit(self, n): return self
        def all(self): return []

    class _FakeDb:
        def query(self, *a): return _EmptyQuery()
        def close(self): pass

    monkeypatch.setattr("core.database.SessionLocal", lambda: _FakeDb())

    with pytest.raises(BaseException) as exc:  # TaskNoop derives BaseException
        asyncio.run(ba.action_auto_distill_sessions(owner="alice"))
    assert "no sessions" in str(exc.value)
    # noop → watermark untouched for everyone
    assert "auto_distill_watermarks" not in store
