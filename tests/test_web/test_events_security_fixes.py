"""Regression tests for SSE snapshot starvation + symlink-loop hardening.

Covers:
- ``_event_generator``: a continuous stream of hub events must not starve the
  periodic status snapshot (the old ``continue`` after each event skipped the
  time-based snapshot check).
- ``resolve_within``: a symlink loop raises ``RuntimeError`` from
  ``Path.resolve`` — it must be translated to ``ValueError`` like other
  unresolvable paths instead of escaping the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from iterate_harness.web import events as events_module
from iterate_harness.web.hub import hub
from iterate_harness.web.security import resolve_within


def _event_type(chunk: str) -> str:
    return chunk.split("\n", 1)[0].split(":", 1)[1].strip()


# ---------------------------------------------------------------------------
# Defect 6: continuous hub events starve the periodic status snapshot
# ---------------------------------------------------------------------------


class TestSnapshotNotStarved:
    @pytest.mark.asyncio
    async def test_status_snapshot_emitted_amid_event_burst(
        self, tmp_path: Path, monkeypatch
    ):
        """A fast stream of hub events must not prevent the periodic status
        push: the snapshot check is time-based and runs even when an event
        was just consumed."""
        monkeypatch.setattr(events_module, "_HUB_WAKEUP", 0.005)
        monkeypatch.setattr(events_module, "_POLL_INTERVAL", 0.04)

        async def pump() -> None:
            while True:
                await hub.publish("chat-message", {"tick": time.monotonic()})
                await asyncio.sleep(0.001)

        pump_task = asyncio.create_task(pump())
        gen = events_module._event_generator(tmp_path, stream_all=False)
        try:
            types: list[str] = []
            for _ in range(80):
                chunk = await asyncio.wait_for(anext(gen), timeout=2.0)
                types.append(_event_type(chunk))
            assert "status" in types, f"status was starved by {len(types)} events"
            # The burst of chat-message events is still streamed promptly.
            assert types.count("chat-message") > 10
        finally:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            await gen.aclose()

    @pytest.mark.asyncio
    async def test_snapshot_interval_still_respected(self, tmp_path: Path, monkeypatch):
        """With no events, snapshots keep their cadence (no busy-loop spam)."""
        monkeypatch.setattr(events_module, "_HUB_WAKEUP", 0.005)
        monkeypatch.setattr(events_module, "_POLL_INTERVAL", 0.1)

        gen = events_module._event_generator(tmp_path, stream_all=False)
        try:
            first = await anext(gen)
            assert _event_type(first) == "status"
            # Next snapshot only after the interval elapses.
            second = await asyncio.wait_for(anext(gen), timeout=2.0)
            assert _event_type(second) == "status"
        finally:
            await gen.aclose()


# ---------------------------------------------------------------------------
# Defect 7: symlink loop raises RuntimeError from Path.resolve
# ---------------------------------------------------------------------------


class TestResolveWithinSymlinkLoop:
    def test_rejects_symlink_loop(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.symlink_to(b, target_is_directory=True)
        b.symlink_to(a, target_is_directory=True)
        with pytest.raises(ValueError, match="cannot resolve"):
            resolve_within(tmp_path, "a")

    def test_rejects_self_symlink_loop(self, tmp_path: Path):
        loop = tmp_path / "loop"
        loop.symlink_to(loop)
        with pytest.raises(ValueError, match="cannot resolve"):
            resolve_within(tmp_path, "loop")
