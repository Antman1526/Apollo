"""End-to-end collector test against a REAL websocket server.

The unit tests inject a fake ws_connect; this exercises the default
websockets-library path (URL building, the additional_headers handshake on
websockets>=14, frame iteration) that fakes cannot validate.
"""
import asyncio
import json

import websockets

from services.paperclip.collector import PaperclipCollector
from services.paperclip.config import PaperclipConfig
from services.paperclip.events import EventHub


def _cfg(port: int) -> PaperclipConfig:
    return PaperclipConfig(
        enabled=True, mode="external", url=f"http://127.0.0.1:{port}",
        browser_url="", port=port,
        model_endpoint="ollama", model_base_url="", model_name="",
    )


def test_collector_consumes_a_real_websocket_server():
    seen = {}

    async def handler(connection):
        seen["path"] = connection.request.path
        seen["auth"] = connection.request.headers.get("Authorization")
        await connection.send(json.dumps({
            "id": "e1", "companyId": "c-live", "type": "agent.status",
            "payload": {"agentId": "a1", "name": "Live Agent", "status": "running"},
        }))
        await connection.send(json.dumps({"type": "plugin.ui.updated", "payload": {}}))
        await connection.send(json.dumps({
            "type": "heartbeat.run.log",
            "payload": {"agentId": "a1", "chunk": "hello from live ws"},
        }))
        # Hold the socket open briefly so the collector reads everything.
        await asyncio.sleep(0.3)

    async def run():
        hub = EventHub()
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            collector = PaperclipCollector(
                _cfg(port), hub.publish,
                token="live-key", company_id="c-live",
                min_backoff=0.05, max_backoff=0.1,
            )
            collector.start()
            for _ in range(400):
                if len(hub.recent) >= 2:
                    break
                await asyncio.sleep(0.01)
            await collector.stop()
        return hub.recent

    recent = asyncio.run(run())
    types = [event["type"] for _seq, event in recent]
    assert types[:2] == ["agent.status", "heartbeat.run.log"]
    assert recent[0][1]["payload"]["agentId"] == "a1"
    assert seen["path"] == "/api/companies/c-live/events/ws"
    assert seen["auth"] == "Bearer live-key"
