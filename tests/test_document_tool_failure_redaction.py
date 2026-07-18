"""Document tool failures must not echo database exception details."""

import asyncio
import sys
import types

from src.tools import documents


def test_invalid_json_is_not_classified_as_a_json_document():
    assert documents._sniff_doc_language("{not valid JSON}") == "markdown"


def test_update_failure_returns_generic_error_and_rolls_back(monkeypatch, caplog):
    class FailingDb:
        rolled_back = False

        def query(self, _model):
            raise RuntimeError("database URL contains secret material")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    db = FailingDb()
    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: db
    database.Document = object
    database.DocumentVersion = object
    monkeypatch.setitem(sys.modules, "src.database", database)

    result = asyncio.run(documents.do_update_document("new content", owner="alice"))

    assert result == {"error": "Failed to update document"}
    assert db.rolled_back is True
    assert "secret material" not in caplog.text
    assert "document_update_failed" in caplog.text
