"""Unexpected browser-tool failures must not expose backend details."""

import asyncio

from services.browser import embedded_browser
from src.tools import web


def test_browser_tool_redacts_unexpected_session_failure(monkeypatch, caplog):
    class FailingSession:
        async def navigate(self, _url):
            raise RuntimeError("browser backend included sensitive response text")

    monkeypatch.setattr(embedded_browser, "session", FailingSession())

    result = asyncio.run(web.do_browser('{"action":"navigate","url":"https://example.com"}'))

    assert result == {"error": "browser action failed", "exit_code": 1}
    assert "sensitive response text" not in caplog.text
    assert "browser_tool_action_failed" in caplog.text
