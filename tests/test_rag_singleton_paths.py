"""RAG persistence must follow the portable runtime data-root contract."""

from __future__ import annotations

import src.rag_singleton as rag_singleton


def test_default_rag_store_follows_application_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_DATA_DIR", str(tmp_path / "state"))

    assert rag_singleton._persist_dir() == str(tmp_path / "state" / "rag")
