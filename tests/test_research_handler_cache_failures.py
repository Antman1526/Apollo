"""Regression coverage for recoverable research-cache failures."""

import logging

from services.research import research_handler as handler_module


def test_corrupt_cache_and_cleanup_failures_are_reported(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=handler_module.__name__)
    monkeypatch.setattr(handler_module, "RESEARCH_DATA_DIR", tmp_path)
    handler = handler_module.ResearchHandler()
    cache_path = tmp_path / "session-1.json"
    cache_path.write_text("not-json", encoding="utf-8")

    assert handler.get_status("session-1") is None
    assert handler.get_result("session-1") is None
    assert handler.get_sources("session-1") is None

    cache_path.unlink()
    cache_path.mkdir()
    handler.clear_result("session-1")

    assert "research_status_cache_read_failed" in caplog.text
    assert "research_result_cache_read_failed" in caplog.text
    assert "research_sources_cache_read_failed" in caplog.text
    assert "research_result_cleanup_failed" in caplog.text
