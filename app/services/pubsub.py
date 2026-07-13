"""
Minimal in-process pub/sub used to stream run progress to SSE subscribers.

This is intentionally simple: it's process-local (fine for a single-instance
take-home), and events are *also* persisted as Step rows, so a client that
connects to /events after some steps have already run still gets a correct
picture - the endpoint replays persisted steps before tailing live ones.
A real multi-instance deployment would back this with Redis pub/sub or a
DB LISTEN/NOTIFY-style mechanism instead; called out in the README as a cut.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class RunEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, event: dict) -> None:
        for queue in list(self._subscribers.get(run_id, [])):
            await queue.put(event)


event_bus = RunEventBus()
