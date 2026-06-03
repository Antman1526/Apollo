import sys
import types
from unittest.mock import MagicMock

# Stub core.database before registry imports it (avoids SQLAlchemy env issues)
if "core.database" not in sys.modules:
    _core_db = types.ModuleType("core.database")
    for _name in [
        "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
        "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
        "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
        "McpServer",
    ]:
        setattr(_core_db, _name, MagicMock())
    sys.modules["core.database"] = _core_db

from services.localmodels.scanner import LocalModel
from services.localmodels import registry


class _FakeEP:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.committed = False

    def query(self, *a):
        return _FakeQuery(self.rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _models():
    return [
        LocalModel("a", "Qwen3.5-9B-Q4_K_M", "/m/a.gguf", "Q4_K_M", "chat", 1, "/m"),
        LocalModel("e", "nomic-embed", "/m/e.gguf", "F16", "embedding", 1, "/m"),
    ]


def test_creates_managed_endpoint_when_absent(monkeypatch):
    sess = _FakeSession(rows=[])
    monkeypatch.setattr(registry, "SessionLocal", lambda: sess)
    monkeypatch.setattr(registry, "ModelEndpoint", _FakeEP)
    registry.sync_managed_endpoint(_models())
    assert len(sess.added) == 1
    ep = sess.added[0]
    assert ep.base_url == registry.LOCAL_BASE_URL
    assert "Qwen3.5-9B-Q4_K_M" in ep.cached_models
    assert sess.committed is True


def test_updates_existing_endpoint(monkeypatch):
    existing = _FakeEP(base_url=registry.LOCAL_BASE_URL, cached_models="[]",
                       is_enabled=False)
    sess = _FakeSession(rows=[existing])
    monkeypatch.setattr(registry, "SessionLocal", lambda: sess)
    monkeypatch.setattr(registry, "ModelEndpoint", _FakeEP)
    registry.sync_managed_endpoint(_models())
    assert sess.added == []
    assert existing.is_enabled is True
    assert "nomic-embed" in existing.cached_models
