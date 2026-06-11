"""Shared Paperclip Floor event plumbing.

Framework-free so both the API routes (routes/paperclip_routes.py) and the
live-events collector (services/paperclip/collector.py) can publish into the
same hub without import cycles.
"""
from __future__ import annotations

import asyncio
from collections import deque

# Event types the Floor UI (static/js/paperclip.js) knows how to render.
FLOOR_EVENT_TYPES = frozenset({
    "agent.status",
    "heartbeat.run.queued",
    "heartbeat.run.status",
    "heartbeat.run.log",
    "heartbeat.run.event",
    "activity.logged",
})


class EventHub:
    """Fan-out hub feeding /api/paperclip/stream.

    Fed by /api/paperclip/events (HTTP ingest) and the live-events collector.
    Keeps a small replay buffer so a Floor view opened after activity started
    still sees recent context. Slow subscribers drop events rather than
    back-pressuring publishers.
    """

    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque = deque(maxlen=history)
        self._seq = 0

    def publish(self, events: list[dict]) -> int:
        accepted = 0
        for event in events:
            self._seq += 1
            entry = (self._seq, event)
            self._recent.append(entry)
            accepted += 1
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass
        return accepted

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def recent(self) -> list[tuple[int, dict]]:
        """Buffered (seq, event) pairs, oldest first."""
        return list(self._recent)
