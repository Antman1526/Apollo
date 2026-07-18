"""Agent-facing research/contact tools must not expose downstream errors."""

import asyncio

import httpx

from src.tools import research_contacts


def test_trigger_research_redacts_client_exception(monkeypatch, caplog):
    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            raise RuntimeError("endpoint returned credential-bearing error")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)

    result = asyncio.run(research_contacts.do_trigger_research('{"topic":"test"}', owner="alice"))

    assert result == {"error": "Research could not be started", "exit_code": 1}
    assert "credential-bearing" not in caplog.text
    assert "research_trigger_failed" in caplog.text
