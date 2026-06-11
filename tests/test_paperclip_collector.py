"""Tests for the Phase-3 live-events collector (services/paperclip/collector)."""
import asyncio
import json

import httpx

from services.paperclip.collector import (
    PaperclipCollector,
    normalize_live_event,
    ws_events_url,
)
from services.paperclip.config import PaperclipConfig


def _cfg(url="http://upstream"):
    return PaperclipConfig(
        enabled=True, mode="native", url=url,
        browser_url="http://localhost:3100", port=3100,
        model_endpoint="ollama", model_base_url="http://x/v1", model_name="",
    )


def test_ws_events_url_upgrades_scheme():
    assert ws_events_url("http://upstream", "c1") == "ws://upstream/api/companies/c1/events/ws"
    assert ws_events_url("https://pc.example/", "c2") == "wss://pc.example/api/companies/c2/events/ws"


def test_normalize_accepts_floor_events_and_filters_the_rest():
    live = json.dumps({
        "id": "e1", "companyId": "c1", "type": "agent.status",
        "createdAt": "2026-06-10T00:00:00Z",
        "payload": {"agentId": "a1", "status": "running"},
    })
    event = normalize_live_event(live)
    assert event["type"] == "agent.status"
    assert event["payload"] == {"agentId": "a1", "status": "running"}
    assert "received_at" in event

    assert normalize_live_event(json.dumps({"type": "plugin.ui.updated", "payload": {}})) is None
    assert normalize_live_event("not json") is None
    assert normalize_live_event(42) is None
    # Non-dict payloads are coerced to an empty payload, not dropped.
    assert normalize_live_event(json.dumps({"type": "activity.logged", "payload": "x"}))["payload"] == {}
    # Bytes frames work too.
    assert normalize_live_event(live.encode())["type"] == "agent.status"


def test_discover_companies_sends_bearer_and_parses_both_shapes():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": "c1"}, {"id": "c2"}, {"name": "no-id"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = PaperclipCollector(_cfg(), lambda evs: 0, token="sekrit", http_client=client)
    ids = asyncio.run(collector.discover_companies())
    assert ids == ["c1", "c2"]
    assert seen["auth"] == "Bearer sekrit"
    assert seen["url"] == "http://upstream/api/companies"

    def wrapped(request):
        assert request.headers.get("authorization") is None
        return httpx.Response(200, json={"companies": [{"id": "c9"}]})

    client2 = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    collector2 = PaperclipCollector(_cfg(), lambda evs: 0, http_client=client2)
    assert asyncio.run(collector2.discover_companies()) == ["c9"]


class _FakeWS:
    def __init__(self, messages):
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeConnect:
    def __init__(self, ws_or_exc):
        self._ws_or_exc = ws_or_exc

    async def __aenter__(self):
        if isinstance(self._ws_or_exc, Exception):
            raise self._ws_or_exc
        return self._ws_or_exc

    async def __aexit__(self, *args):
        return False


def test_run_publishes_normalized_events():
    published = []
    messages = [
        json.dumps({"type": "agent.status", "payload": {"agentId": "a1", "status": "running"}}),
        json.dumps({"type": "plugin.worker.crashed", "payload": {}}),
        json.dumps({"type": "heartbeat.run.log", "payload": {"agentId": "a1", "chunk": "hi"}}),
    ]

    def ws_connect(url, headers):
        assert url == "ws://upstream/api/companies/c1/events/ws"
        assert headers == {}
        return _FakeConnect(_FakeWS(messages))

    collector = PaperclipCollector(
        _cfg(), lambda evs: published.extend(evs) or len(evs),
        company_id="c1", ws_connect=ws_connect,
        min_backoff=0.01, max_backoff=0.02,
    )

    async def run():
        task = asyncio.get_event_loop().create_task(collector.run())
        for _ in range(200):
            if len(published) >= 2:
                break
            await asyncio.sleep(0.005)
        await collector_stop(collector, task)

    asyncio.run(run())
    assert [e["type"] for e in published[:2]] == ["agent.status", "heartbeat.run.log"]


def test_run_retries_after_connection_failure():
    published = []
    attempts = {"n": 0}

    def ws_connect(url, headers):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeConnect(ConnectionError("refused"))
        return _FakeConnect(_FakeWS([
            json.dumps({"type": "agent.status", "payload": {"agentId": "a1"}}),
        ]))

    collector = PaperclipCollector(
        _cfg(), lambda evs: published.extend(evs) or len(evs),
        company_id="c1", ws_connect=ws_connect,
        min_backoff=0.01, max_backoff=0.02,
    )

    async def run():
        task = asyncio.get_event_loop().create_task(collector.run())
        for _ in range(400):
            if published:
                break
            await asyncio.sleep(0.005)
        await collector_stop(collector, task)

    asyncio.run(run())
    assert attempts["n"] >= 2
    assert published and published[0]["type"] == "agent.status"


def test_stop_cancels_the_task():
    def ws_connect(url, headers):
        # Never-ending stream: a websocket that hangs forever on read.
        class _Hang:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(3600)

        return _FakeConnect(_Hang())

    collector = PaperclipCollector(
        _cfg(), lambda evs: 0, company_id="c1", ws_connect=ws_connect,
        min_backoff=0.01,
    )

    async def run():
        task = collector.start()
        await asyncio.sleep(0.02)
        assert collector.status()["running"] is True
        await collector.stop()
        assert task.done()
        assert collector.status()["running"] is False

    asyncio.run(run())


async def collector_stop(collector, task):
    await collector.stop()
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
