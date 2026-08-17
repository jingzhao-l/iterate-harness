"""Tests for the ChatHub in-process pub/sub hub (design §18).

Covers:
- subscribe/unsubscribe registration
- publish fan-out to multiple subscribers
- bounded queue dropping oldest events when full
- fan-out isolation: a full subscriber never blocks the publisher or other
  subscribers (the run loop must never stall on a slow client)
"""

from __future__ import annotations

import asyncio

import pytest

import iterate_harness.web.hub as hub_module
from iterate_harness.web.hub import ChatHub, HubEvent, hub


@pytest.mark.asyncio
async def test_subscribe_adds_subscriber():
    ch = ChatHub()
    q = await ch.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert q.maxsize == 200  # _QUEUE_CAP
    assert len(ch._subscribers) == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscriber():
    ch = ChatHub()
    q = await ch.subscribe()
    assert len(ch._subscribers) == 1
    await ch.unsubscribe(q)
    assert len(ch._subscribers) == 0


@pytest.mark.asyncio
async def test_unsubscribe_unknown_queue_is_no_op():
    ch = ChatHub()
    q = await ch.subscribe()
    other = asyncio.Queue()
    await ch.unsubscribe(other)  # not a registered subscriber
    assert len(ch._subscribers) == 1
    await ch.unsubscribe(q)
    assert len(ch._subscribers) == 0


@pytest.mark.asyncio
async def test_publish_fan_out():
    ch = ChatHub()
    q1 = await ch.subscribe()
    q2 = await ch.subscribe()

    await ch.publish("test-event", {"key": "value"})
    e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    e2 = await asyncio.wait_for(q2.get(), timeout=0.5)

    assert isinstance(e1, HubEvent)
    assert e1.type == "test-event"
    assert e1.data == {"key": "value"}
    assert e2.type == "test-event"
    assert e1 == e2


@pytest.mark.asyncio
async def test_publish_drops_nothing_when_not_full(monkeypatch):
    monkeypatch.setattr(hub_module, "_QUEUE_CAP", 3)
    ch = ChatHub()
    q = await ch.subscribe()
    for i in range(3):
        await ch.publish(f"ev{i}", {"i": i})
    assert q.qsize() == 3
    events = [q.get_nowait() for _ in range(3)]
    assert [e.type for e in events] == ["ev0", "ev1", "ev2"]


@pytest.mark.asyncio
async def test_full_queue_drops_oldest_to_make_room(monkeypatch):
    monkeypatch.setattr(hub_module, "_QUEUE_CAP", 2)
    ch = ChatHub()
    q = await ch.subscribe()
    await ch.publish("ev1", {"n": 1})
    await ch.publish("ev2", {"n": 2})
    assert q.qsize() == 2

    # Publish a third — queue is full, so the oldest (ev1) is dropped and
    # the newest (ev3) lands.
    await ch.publish("ev3", {"n": 3})
    assert q.qsize() == 2
    events = [q.get_nowait() for _ in range(2)]
    assert [e.data["n"] for e in events] == [2, 3]


@pytest.mark.asyncio
async def test_full_subscriber_does_not_block_others(monkeypatch):
    monkeypatch.setattr(hub_module, "_QUEUE_CAP", 1)
    ch = ChatHub()
    q_full = await ch.subscribe()
    await ch.publish("pre", {})  # fills q_full
    q_empty = await ch.subscribe()

    # New event: q_full drops the old one; q_empty gets the new one.
    await ch.publish("live", {"v": 42})

    got_full = q_full.get_nowait()
    got_empty = q_empty.get_nowait()
    assert got_full.type == "live"
    assert got_empty.type == "live"
    assert got_full.data["v"] == 42


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_no_op():
    ch = ChatHub()
    await ch.publish("no-op", {"x": 1})  # must not raise


@pytest.mark.asyncio
async def test_module_singleton_roundtrip():
    q = await hub.subscribe()
    await hub.publish("ping", {"msg": "hello"})
    event = await asyncio.wait_for(q.get(), timeout=0.5)
    assert event.type == "ping"
    assert event.data["msg"] == "hello"
    await hub.unsubscribe(q)
    assert len(hub._subscribers) == 0


@pytest.mark.asyncio
async def test_hub_event_dataclass_immutable():
    event = HubEvent(type="t", data={"a": 1})
    assert event.type == "t"
    assert event.data == {"a": 1}
    with pytest.raises(AttributeError):
        event.type = "changed"  # type: ignore[misc]
