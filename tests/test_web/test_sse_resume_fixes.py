"""Regression tests for SSE reconnect resume (hub-global ids + Last-Event-ID).

Covers:

- Hub ids are global and monotonic across subscribers: one publish delivers
  one shared id, ids only ever increase (fix 11).
- A reconnecting SSE stream with ``Last-Event-ID`` replays the still-buffered
  events published after that id (fix 11).
- A ``Last-Event-ID`` too old to replay yields an explicit ``sse-gap`` notice
  as the first frame instead of silently skipping events (fix 11).
- ``ChatHub.resume_after`` completeness semantics against the bounded ring.
"""

from __future__ import annotations

import asyncio

from iterate_harness.web import events as events_module
from iterate_harness.web.hub import ChatHub, hub


def _frame_field(chunk: str, field: str) -> str:
    for line in chunk.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _event_type(chunk: str) -> str:
    return _frame_field(chunk, "event")


# ---------------------------------------------------------------------------
# Hub-global monotonic ids (fix 11)
# ---------------------------------------------------------------------------


class TestHubGlobalIds:
    async def test_one_publish_one_shared_id(self):
        ch = ChatHub()
        q1 = await ch.subscribe()
        q2 = await ch.subscribe()
        await ch.publish("chat-message", {"n": 1})
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1.id > 0
        assert e1.id == e2.id  # the id belongs to the hub, not the connection

    async def test_ids_increase_across_publishes(self):
        ch = ChatHub()
        q = await ch.subscribe()
        await ch.publish("a", {})
        await ch.publish("b", {})
        first = q.get_nowait()
        second = q.get_nowait()
        assert first.id < second.id
        assert ch.current_event_id() == second.id

    async def test_status_frames_share_the_hub_counter(self):
        """Locally generated SSE frames (status snapshots) allocate from the
        same monotonic counter as hub events."""
        before = hub.current_event_id()
        await hub.publish("chat-message", {"probe": True})
        after_publish = hub.current_event_id()
        allocated = hub.next_event_id()
        assert before < after_publish < allocated


# ---------------------------------------------------------------------------
# Last-Event-ID replay / gap (fix 11)
# ---------------------------------------------------------------------------


class TestLastEventIdResume:
    async def test_replay_missed_event_after_reconnect(self, tmp_path):
        await hub.publish("chat-message", {"x": 1}, project_root=str(tmp_path))
        event_id = hub.current_event_id()
        gen = events_module._event_generator(
            tmp_path, stream_all=False, last_event_id=event_id - 1
        )
        try:
            chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
        finally:
            await gen.aclose()
        assert _event_type(chunk) == "chat-message"
        assert _frame_field(chunk, "id") == str(event_id)
        assert '"x": 1' in chunk

    async def test_current_last_id_resumes_without_gap_or_replay(self, tmp_path):
        await hub.publish("chat-message", {"x": 2}, project_root=str(tmp_path))
        gen = events_module._event_generator(
            tmp_path, stream_all=False, last_event_id=hub.current_event_id()
        )
        try:
            chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
        finally:
            await gen.aclose()
        # Nothing was lost, so the stream continues with the periodic status
        # snapshot — no gap notice, no replay.
        assert _event_type(chunk) == "status"

    async def test_evicted_last_id_emits_sse_gap_notice(self, tmp_path, monkeypatch):
        """A client away longer than the bounded replay window cannot be
        replayed honestly — it gets an explicit sse-gap notice instead."""
        monkeypatch.setattr("iterate_harness.web.hub._REPLAY_BUFFER", 5)
        first_id = hub.current_event_id() + 1
        for i in range(10):  # 10 publishes > buffer of 5 → first ids evicted
            await hub.publish("chat-message", {"i": i}, project_root=str(tmp_path))
        gen = events_module._event_generator(
            tmp_path, stream_all=False, last_event_id=first_id
        )
        try:
            chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
        finally:
            await gen.aclose()
        assert _event_type(chunk) == "sse-gap"
        assert '"reason": "unresumable"' in chunk

    async def test_no_last_event_id_starts_fresh_without_gap(self, tmp_path):
        gen = events_module._event_generator(tmp_path, stream_all=False)
        try:
            chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
        finally:
            await gen.aclose()
        assert _event_type(chunk) == "status"


class TestResumeAfter:
    async def test_complete_within_buffer(self):
        ch = ChatHub()
        ids = []
        for i in range(3):
            await ch.publish("ev", {"i": i})
            ids.append(ch.current_event_id())
        missed, complete = ch.resume_after(ids[0])
        assert complete is True
        assert [event.id for event in missed] == ids[1:]

    async def test_incomplete_after_eviction(self, monkeypatch):
        monkeypatch.setattr("iterate_harness.web.hub._REPLAY_BUFFER", 2)
        ch = ChatHub()
        await ch.publish("ev", {"i": 0})
        oldest_id = ch.current_event_id()
        for i in range(1, 4):  # 4 publishes, buffer keeps the last 2
            await ch.publish("ev", {"i": i})
        missed, complete = ch.resume_after(oldest_id - 1)
        assert complete is False
        assert all(event.id > oldest_id - 1 for event in missed)

    async def test_future_id_treated_as_up_to_date(self):
        ch = ChatHub()
        await ch.publish("ev", {})
        missed, complete = ch.resume_after(ch.current_event_id() + 100)
        assert missed == []
        assert complete is True
