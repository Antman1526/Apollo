"""Regression coverage for detached agent-run delivery behavior."""

import asyncio

from src import agent_runs


def test_publish_keeps_replay_buffer_when_subscriber_queue_is_full():
    class FullQueue:
        def put_nowait(self, _item):
            raise asyncio.QueueFull

    run = agent_runs._Run()
    run.subscribers.add(FullQueue())

    agent_runs._publish(run, "data: update\n\n")

    assert run.buffer == ["data: update\n\n"]
