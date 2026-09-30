"""Regression tests for the WebUI UX hardening pass (design §17.4 / §18.4).

Covers the behaviour added while reviewing the operator console against its
two channels (the console itself + the auxiliary SSE/intervention channel):

- SSE frames carry a monotonic ``id:`` so a reconnecting ``EventSource`` can
  send ``Last-Event-ID`` and both sides can reason about gaps.
- Hub subscriptions are reaped once their owning event loop is gone (a
  connection torn down without unwinding its generator must not leak a queue
  that every later publish fans out into), with a hard subscriber cap as a
  backstop.
- An unhandled route exception is returned through the API's ``{"detail": ...}``
  JSON contract instead of Starlette's non-JSON 500, so the console's fetch
  wrapper can decode it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.web import events as events_module
from iterate_harness.web.api import create_app
from iterate_harness.web.hub import ChatHub, hub as module_hub


# ---------------------------------------------------------------------------
# SSE event ids
# ---------------------------------------------------------------------------


class TestSSEEventIds:
    def test_sse_frame_contains_id_event_data(self):
        frame = events_module._sse_frame("status", {"round": 3}, 7)
        assert "id: 7" in frame
        assert "event: status" in frame
        assert '"round": 3' in frame
        # The frame must be terminated by a blank line to flush it.
        assert frame.endswith("\n\n")

    def test_sse_frame_omits_id_when_none(self):
        frame = events_module._sse_frame("ping", {})
        assert "id:" not in frame

    @pytest.mark.asyncio
    async def test_generator_ids_increase_monotonically(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(events_module, "_POLL_INTERVAL", 0.0)
        gen = events_module._event_generator(tmp_path, stream_all=True)
        try:
            ids = []
            for _ in range(4):
                chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
                id_line = next(
                    line for line in chunk.splitlines() if line.startswith("id:")
                )
                ids.append(int(id_line.split(":", 1)[1].strip()))
            assert ids == sorted(ids)
            assert len(set(ids)) == len(ids)
        finally:
            await gen.aclose()


# ---------------------------------------------------------------------------
# Hub subscriber reaping
# ---------------------------------------------------------------------------


class TestHubReaping:
    @pytest.mark.asyncio
    async def test_reap_drops_subscribers_from_closed_loops(self):
        """A subscription whose loop was torn down without unsubscribing is
        dropped on the next sweep — otherwise every later publish fans out into
        a queue nobody reads."""
        ch = ChatHub()
        stale = await ch.subscribe("/project-a")
        assert len(ch._subscribers) == 1

        ch._subscribers[stale].loop = _closed_loop()
        assert ch.reap() == 1
        assert len(ch._subscribers) == 0

    @pytest.mark.asyncio
    async def test_subscribe_reaps_stale(self):
        ch = ChatHub()
        stale = await ch.subscribe()
        # Pretend the owning loop died without the generator unwinding.
        ch._subscribers[stale].loop = _closed_loop()
        await ch.subscribe()  # any new subscriber triggers the sweep
        assert stale not in ch._subscribers
        assert len(ch._subscribers) == 1

    @pytest.mark.asyncio
    async def test_subscriber_cap_evicts_oldest(self, monkeypatch):
        """Even with a live loop, a reconnect storm cannot grow the subscriber
        set without bound: past the cap the oldest is evicted."""
        monkeypatch.setattr("iterate_harness.web.hub._MAX_SUBSCRIBERS", 3)
        ch = ChatHub()
        queues = [await ch.subscribe() for _ in range(5)]
        assert len(ch._subscribers) == 3
        # The two oldest were evicted; the newest survive and still receive.
        assert queues[0] not in ch._subscribers
        assert queues[1] not in ch._subscribers
        assert queues[-1] in ch._subscribers

    @pytest.mark.asyncio
    async def test_reap_keeps_live_subscribers(self):
        ch = ChatHub()
        live = await ch.subscribe()
        assert ch.reap() == 0
        assert live in ch._subscribers

    @pytest.mark.asyncio
    async def test_reap_is_idempotent(self):
        ch = ChatHub()
        stale = await ch.subscribe()
        ch._subscribers[stale].loop = _closed_loop()
        assert ch.reap() == 1
        assert ch.reap() == 0


def _closed_loop() -> asyncio.AbstractEventLoop:
    """Return a minimal object that reports ``is_closed() -> True``."""
    loop = asyncio.new_event_loop()
    loop.close()
    return loop


# ---------------------------------------------------------------------------
# Unhandled-exception JSON contract
# ---------------------------------------------------------------------------


class TestUnhandledExceptionContract:
    def test_unhandled_error_returns_json_detail(self, tmp_path: Path, monkeypatch):
        """A bug in any route must reach the console through the API's
        ``{"detail": ...}`` contract, not Starlette's plain-text 500 (which the
        frontend's JSON decode drops, leaving a bare "HTTP 500")."""
        from iterate_harness.web.routes import status as status_routes

        def _boom(_root: Path) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr(status_routes, "read_entries", _boom)
        # ``raise_server_exceptions=False`` so the TestClient surfaces the
        # handler's response instead of re-raising the original error.
        client = TestClient(create_app(project_root=tmp_path), raise_server_exceptions=False)
        response = client.get("/api/v1/status", params={"project_root": str(tmp_path)})
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        assert "RuntimeError" in detail
        assert "kaboom" in detail

    def test_run_manager_error_still_maps_to_409(self, tmp_path: Path):
        """The specific handler must keep winning over the catch-all: Starlette
        resolves handlers by exception-class specificity, so registering
        ``Exception`` must not downgrade a rejected run operation from 409 to
        a generic 500."""
        from iterate_harness.web.run_manager import RunManagerError

        app = create_app(project_root=tmp_path)
        handler = app.exception_handlers[RunManagerError]
        response = asyncio.run(handler(None, RunManagerError("已有运行中的 iterate 循环")))
        assert response.status_code == 409
        assert b"409" in response.body or "已有运行中" in response.body.decode()


# ---------------------------------------------------------------------------
# Frontend-visible tool-message persistence helper contract
# ---------------------------------------------------------------------------


def test_publish_tool_writes_history_entry(tmp_path: Path):
    """Tool activity is broadcast AND persisted.

    Regression: tool messages were broadcast live but never appended to
    ``web-chat.jsonl``, so a page reload (which re-reads history) lost every
    "▶ 调用工具 X" line the operator had already seen.
    """
    from iterate_harness.web.run_manager import RunManager

    rm = RunManager()
    rm._chat_dir = tmp_path / ".iterate"
    rm.project_root = str(tmp_path)

    asyncio.run(rm._publish_tool("▶ 调用工具 Read"))

    history = rm.history()
    tool_msgs = [m for m in history if m.get("kind") == "tool"]
    assert len(tool_msgs) == 1
    assert "Read" in tool_msgs[0]["content"]


def test_history_entry_is_valid_json_line(tmp_path: Path):
    from iterate_harness.web.run_manager import RunManager

    rm = RunManager()
    rm._chat_dir = tmp_path / ".iterate"
    chat_file = tmp_path / ".iterate" / "web-chat.jsonl"

    asyncio.run(rm._publish_chat("system", "hello", kind="status"))
    asyncio.run(rm._publish_tool("✔ Bash: done"))

    lines = chat_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["status", "tool"]


def test_module_singleton_hub_reap_is_callable():
    assert callable(module_hub.reap)
