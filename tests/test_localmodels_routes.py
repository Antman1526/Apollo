import sys
import types
from unittest.mock import MagicMock

# Stub core.database before lifecycle imports registry (avoids SQLAlchemy env issues)
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

from services.localmodels import lifecycle


def test_rescan_returns_catalog_and_syncs(monkeypatch):
    from services.localmodels.scanner import LocalModel
    fake = [LocalModel("a", "ModelA", "/m/a.gguf", "Q4_K_M", "chat", 1, "/m")]
    monkeypatch.setattr(lifecycle, "scan_dirs", lambda dirs: fake)
    monkeypatch.setattr(lifecycle, "get_local_model_dirs", lambda: ["/m"])
    synced = {}
    monkeypatch.setattr(lifecycle, "sync_managed_endpoint",
                        lambda models: synced.setdefault("n", len(models)))
    result = lifecycle.rescan()
    assert [m.name for m in result] == ["ModelA"]
    assert synced["n"] == 1
