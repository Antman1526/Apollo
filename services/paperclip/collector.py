"""Phase-3 collector: bridge Paperclip's live-events WebSocket into the Floor.

Connects to Paperclip's realtime endpoint
(`/api/companies/{companyId}/events/ws`), normalizes each LiveEvent, and
publishes it into the shared EventHub that feeds `/api/paperclip/stream` — so
real Paperclip agents appear in the isometric office without any external
process POSTing to `/api/paperclip/events`.

Auth (verified against Paperclip server source,
`server/src/realtime/live-events-ws.ts` and `server/src/middleware/auth.ts`):

- ``local_trusted`` deployment mode — Paperclip's default and what the
  bundled sidecar runs — accepts tokenless REST and websocket connections
  (implicit instance-admin "board" actor).
- ``authenticated`` mode requires an agent API key passed as a Bearer token
  (header or ``?token=``); set ``PAPERCLIP_COLLECTOR_TOKEN`` for that case.
  The key is company-scoped, so pair it with ``PAPERCLIP_COMPANY_ID``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Optional

import httpx

from services.paperclip.config import PaperclipConfig
from services.paperclip.events import FLOOR_EVENT_TYPES

logger = logging.getLogger(__name__)


def ws_events_url(base_url: str, company_id: str) -> str:
    """Paperclip live-events websocket URL for a company (http(s) -> ws(s))."""
    base = base_url.rstrip("/").replace("http", "ws", 1)
    return f"{base}/api/companies/{company_id}/events/ws"


def normalize_live_event(raw) -> Optional[dict]:
    """Paperclip LiveEvent -> Floor event, or None when not floor-relevant.

    LiveEvent shape: {id, companyId, type, createdAt, payload}. The Floor
    consumes the same type names, so normalization is a filter plus a strict
    payload shape.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    type_ = raw.get("type")
    if type_ not in FLOOR_EVENT_TYPES:
        return None
    payload = raw.get("payload")
    return {
        "type": type_,
        "payload": payload if isinstance(payload, dict) else {},
        "received_at": time.time(),
    }


def _default_ws_connect(url: str, headers: dict):
    import websockets

    try:
        return websockets.connect(url, additional_headers=headers or None)
    except TypeError:  # websockets < 14 spelled it extra_headers
        return websockets.connect(url, extra_headers=headers or None)


class PaperclipCollector:
    """Long-lived background task with capped-backoff reconnects."""

    def __init__(
        self,
        cfg: PaperclipConfig,
        publish: Callable[[list], int],
        *,
        token: str = "",
        company_id: str = "",
        http_client: Optional[httpx.AsyncClient] = None,
        ws_connect=None,
        min_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ):
        self._cfg = cfg
        self._publish = publish
        self._token = token
        self._company_id = company_id
        self._client = http_client
        self._ws_connect = ws_connect or _default_ws_connect
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._warned = False
        self.connected = False

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def discover_companies(self) -> list[str]:
        """Company ids visible to the collector via Paperclip REST."""
        client = self._client
        owns = client is None
        if owns:
            client = httpx.AsyncClient(timeout=10.0)
        try:
            r = await client.get(
                f"{self._cfg.url}/api/companies", headers=self._auth_headers()
            )
            r.raise_for_status()
            data = r.json()
        finally:
            if owns:
                await client.aclose()
        items = data.get("companies") if isinstance(data, dict) else data
        ids = []
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        return ids

    async def _consume(self, company_id: str) -> None:
        url = ws_events_url(self._cfg.url, company_id)
        async with self._ws_connect(url, self._auth_headers()) as ws:
            logger.info("Paperclip collector connected: company %s", company_id)
            self.connected = True
            self._warned = False
            try:
                async for message in ws:
                    event = normalize_live_event(message)
                    if event:
                        self._publish([event])
            finally:
                self.connected = False

    async def run(self) -> None:
        backoff = self._min_backoff
        while not self._stop.is_set():
            session_started = time.monotonic()
            try:
                companies = (
                    [self._company_id] if self._company_id
                    else await self.discover_companies()
                )
                if not companies:
                    raise RuntimeError("no Paperclip companies visible")
                await asyncio.gather(*(self._consume(c) for c in companies))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._warned:
                    logger.warning("Paperclip collector unavailable (will retry): %s", exc)
                    self._warned = True
                else:
                    logger.debug("Paperclip collector retry failed: %s", exc)
            if self._stop.is_set():
                return
            # A session that survived a while was healthy — reset the backoff.
            if time.monotonic() - session_started > 30:
                backoff = self._min_backoff
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self._max_backoff)

    def start(self) -> asyncio.Task:
        """Start (or restart) the collector on the running event loop."""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
            pass

    def status(self) -> dict:
        return {
            "running": self._task is not None and not self._task.done(),
            "connected": self.connected,
            "authenticated": bool(self._token),
        }
