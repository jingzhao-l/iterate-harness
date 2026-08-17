"""In-process pub/sub hub for live WebUI events (design §18).

The SSE endpoint (:mod:`iterate_harness.web.events`) needs to push events
that originate *outside* the file-based poller — the iterate run loop runs
as a background task inside the same process and publishes progress, chat
messages, run-state transitions and interaction requests through this hub.
Every SSE connection subscribes to the hub; the generator interleaves hub
events with the existing file-polling cadence.

The hub is intentionally minimal: one bounded queue per subscriber, a
publish fan-out that never blocks the publisher (full queues drop the
oldest event instead of stalling the run loop). There is a single
module-level singleton because the WebUI keeps at most one live run and the
hub must be reachable from both the route layer and the SSE generator.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HubEvent:
    """One broadcast unit pushed to every subscribed SSE connection."""

    type: str
    data: dict[str, Any]


#: Per-subscriber queue cap. The run loop must never block on a slow client,
#: so full queues drop the oldest event (status snapshots are idempotent and
#: the frontend re-syncs via REST anyway).
_QUEUE_CAP = 200


class ChatHub:
    """Fan-out hub for live WebUI events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[HubEvent]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[HubEvent]:
        """Register a new subscriber; returns its private bounded queue."""
        queue: asyncio.Queue[HubEvent] = asyncio.Queue(maxsize=_QUEUE_CAP)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[HubEvent]) -> None:
        """Drop a subscriber (called when the SSE connection closes)."""
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, type: str, data: dict[str, Any]) -> None:
        """Broadcast one event to every subscriber (never raises)."""
        event = HubEvent(type=type, data=data)
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event so the newest one always lands.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass


#: Module-level singleton shared by routes + the SSE generator.
hub = ChatHub()


__all__ = ["ChatHub", "HubEvent", "hub"]
