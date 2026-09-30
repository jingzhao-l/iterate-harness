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

Subscriptions are reaped when their owning event loop dies, and a hard
subscriber cap evicts the oldest, so a connection torn down without
unwinding its generator cannot leak a queue that every later publish fans
out into.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


def _normalize_root(project_root: str | None) -> str | None:
    """Canonicalize a project root for hub scoping comparisons."""
    if project_root is None:
        return None
    text = str(project_root).strip()
    if not text:
        return None
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(text)))
    except OSError:
        return os.path.normcase(os.path.abspath(os.path.expanduser(text)))


@dataclass(frozen=True)
class HubEvent:
    """One broadcast unit pushed to every subscribed SSE connection."""

    type: str
    data: dict[str, Any]


#: Per-subscriber queue cap. The run loop must never block on a slow client,
#: so full queues drop the oldest event (status snapshots are idempotent and
#: the frontend re-syncs via REST anyway).
_QUEUE_CAP = 200

#: Event types whose loss would desynchronize the frontend's interaction
#: state machine ("paused"+waitingFor drives every permission/select/prompt
#: dialog, and a dropped transition leaves a modal phantom on screen). These
#: are prioritized so drop-oldest eviction never removes them while a
#: disposable event is available.
_PRIORITY_TYPES = frozenset({"run-state"})

#: Safety valve on the subscriber count. Every live SSE connection holds one
#: subscription; a tab that reconnects in a loop (flapping network, laptop
#: sleep) can otherwise pile up dead subscriptions if its generator is torn
#: down without running its ``finally``. Well above the handful of tabs a
#: single-operator local console opens.
_MAX_SUBSCRIBERS = 64


@dataclass
class _Subscription:
    """Bookkeeping for one subscriber: its scope, loop and registration time."""

    scope: str
    loop: asyncio.AbstractEventLoop
    registered_at: float


class ChatHub:
    """Fan-out hub for live WebUI events."""

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[HubEvent], _Subscription] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_root: str | None = None) -> asyncio.Queue[HubEvent]:
        """Register a new subscriber; returns its private bounded queue.

        ``project_root`` scopes the subscription. The hub is a process-global
        singleton but a run belongs to exactly one project: without scoping, a
        tab watching project B received project A's run-state and chat traffic,
        flipped B's banner to "running", and auto-opened A's permission prompt
        over B's transcript. ``None`` means "any project" (the wildcard used
        by callers that genuinely do not know, e.g. a bare status poll).

        Subscriptions left behind by a dead consumer are reaped here (see
        :meth:`reap`): a connection torn down without unwinding its generator
        would otherwise keep receiving publishes forever.
        """
        queue: asyncio.Queue[HubEvent] = asyncio.Queue(maxsize=_QUEUE_CAP)
        scope = _normalize_root(project_root)
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._reap_locked()
            if len(self._subscribers) >= _MAX_SUBSCRIBERS:
                # Still over the cap after reaping: evict the oldest, which is
                # the likeliest leftover of a flapping connection.
                oldest = min(
                    self._subscribers.items(),
                    key=lambda item: item[1].registered_at,
                )[0]
                log.debug("evicting oldest hub subscriber (cap %d reached)", _MAX_SUBSCRIBERS)
                self._subscribers.pop(oldest, None)
            self._subscribers[queue] = _Subscription(
                scope=scope or "", loop=loop, registered_at=time.monotonic()
            )
        return queue

    def reap(self) -> int:
        """Drop subscriptions whose owning event loop is closed; returns the count.

        A subscriber's queue is only useful while the loop that drains it is
        alive. When a loop is torn down without the generator's ``finally``
        running (an aborted task, a test that never unsubscribed), the entry
        lingers and every later publish keeps fanning out into it. Callers on a
        live loop can invoke this directly; :meth:`subscribe` runs the same
        sweep automatically.
        """
        return self._reap_locked()

    def _reap_locked(self) -> int:
        """Reap dead subscriptions. Caller must hold ``self._lock`` (or be the
        sole writer during construction)."""
        dead = [
            queue
            for queue, sub in self._subscribers.items()
            if sub.loop.is_closed()
        ]
        for queue in dead:
            self._subscribers.pop(queue, None)
        return len(dead)

    async def unsubscribe(self, queue: asyncio.Queue[HubEvent]) -> None:
        """Drop a subscriber (called when the SSE connection closes)."""
        async with self._lock:
            self._subscribers.pop(queue, None)

    async def publish(
        self, type: str, data: dict[str, Any], *, project_root: str | None = None
    ) -> None:
        """Broadcast one event to matching subscribers (never raises).

        Events published without a ``project_root`` reach every subscriber
        (process-level notices); events carrying one reach only the subscribers
        scoped to that project (or to the wildcard).
        """
        event = HubEvent(type=type, data=data)
        scope = _normalize_root(project_root)
        async with self._lock:
            subscribers = [
                queue
                for queue, sub in self._subscribers.items()
                if scope is None or not sub.scope or sub.scope == scope
            ]
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Full queue: drop the oldest *disposable* event first (chat /
                # progress re-sync via REST; run-state never drops while a
                # disposable event exists, so a paused-run prompt can't be
                # silently lost behind a chat flood). Only when the whole
                # queue is priority events does the oldest of those give way.
                try:
                    self._drop_oldest_disposable(queue)
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

    @staticmethod
    def _drop_oldest_disposable(queue: asyncio.Queue[HubEvent]) -> None:
        """Evict the oldest non-priority event; fall back to the oldest event.

        Only called when the queue is full. Drains, rebuilds with one event
        dropped, and re-queues everything (order preserved apart from the
        eviction).
        """
        items: list[HubEvent] = []
        while True:
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        drop_index = next(
            (i for i, item in enumerate(items) if item.type not in _PRIORITY_TYPES),
            None,
        )
        if drop_index is None:
            drop_index = 0  # Only priority events: evict the oldest anyway.
        del items[drop_index]
        for item in items:
            queue.put_nowait(item)


#: Module-level singleton shared by routes + the SSE generator.
hub = ChatHub()


__all__ = ["ChatHub", "HubEvent", "hub"]
