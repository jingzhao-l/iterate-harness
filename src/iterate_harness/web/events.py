"""SSE real-time push (design §17.4: events).

Streams incremental updates to the WebUI via Server-Sent Events. Three event
sources are interleaved in one generator:

- ``status`` — periodic (every 5s) re-read of the decision log and cost
  meter; pushes a compact delta payload.
- ``decision-log`` — tail-only: reads only the *new* lines appended since
  the last poll, using a file-position cursor (``SEEK_END`` on first read).
- live hub events (``chat-message`` / ``run-state`` / ``progress-update``) —
  broadcast by the iterate run loop inside the same process (design §18);
  these drive the chat panel without polling.

The implementation is intentionally simple: no message bus, no websocket,
just a generator that yields ``data:`` lines. The frontend reconnects
automatically (``EventSource``).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from ..iterate.checkpoint import load_checkpoint
from ..iterate.decision_log import read_entries
from .hub import hub
from ._coerce import as_float, as_int

router = APIRouter(tags=["events"])

#: Polling interval (seconds) for the background SSE generator.
_POLL_INTERVAL = 5.0

#: Wake-up cadence (seconds) while draining hub events, so the periodic
#: status snapshot is never starved by a chat-heavy burst.
_HUB_WAKEUP = 0.5


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _build_status_payload(project_root: Path) -> dict[str, Any]:
    """Build a compact status snapshot for the SSE stream."""
    entries = read_entries(project_root) or []
    checkpoint = load_checkpoint(project_root) or {}
    latest_round = max((entry.round for entry in entries), default=0)
    report = next(
        (e for e in reversed(entries) if e.type == "report"),
        None,
    )
    report_data = report.data if report is not None and isinstance(report.data, dict) else {}
    checkpoint_converged = bool(checkpoint.get("converged", False)) if checkpoint else None
    converged = report_data.get("converged")
    if converged is None:
        converged = checkpoint_converged
    return {
        "entryCount": len(entries),
        "latestRound": latest_round,
        "checkpointExists": bool(checkpoint),
        "checkpointRound": as_int(checkpoint.get("round")) if checkpoint else 0,
        "totalTokens": as_int(report_data.get("totalTokens")) or as_int(checkpoint.get("input_tokens", 0)),
        "totalCostUsd": as_float(report_data.get("totalCostUsd")) or as_float(checkpoint.get("cost_usd", 0.0)),
        "converged": converged,
        "timestamp": time.time(),
    }


def _decision_log_tail(log_path: Path, cursor: int) -> tuple[list[dict[str, Any]], int]:
    """Read new decision-log lines since ``cursor``; returns (entries, new_cursor).

    On any read error (rotation / deletion) the cursor resets so the next poll
    re-syncs cleanly. A *truncated* journal (new size below the old cursor) is
    treated as a rotation: the cursor re-anchors at the start of the new file,
    so the whole replacement is streamed and the new EOF is picked up instead
    of being permanently skipped past a stale EOF cursor.
    """
    if not log_path.exists():
        return [], 0
    try:
        current_size = log_path.stat().st_size
        if current_size < cursor:
            # Journal was truncated/rotated to a smaller size: re-anchor at the
            # start of the new file (fall through to read the replacement).
            cursor = 0
        if current_size == cursor:
            return [], cursor
        with log_path.open("r", encoding="utf-8") as handle:
            handle.seek(cursor)
            new_lines = handle.read()
            new_cursor = handle.tell()
        entries: list[dict[str, Any]] = []
        for line in new_lines.splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries, new_cursor
    except OSError:
        return [], 0


async def _event_generator(project_root: Path, stream_all: bool) -> Any:
    """Async generator interleaving hub events with periodic file snapshots."""
    log_path = project_root / ".iterate" / "decision-log.jsonl"
    cursor = 0 if not log_path.exists() else log_path.stat().st_size
    queue = await hub.subscribe()
    last_flush = 0.0
    try:
        while True:
            # 1) Drain live hub events (chat / run-state / progress) promptly.
            #    No ``continue`` here: the snapshot below is time-gated so a
            #    steady event stream can never starve the status push.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HUB_WAKEUP)
                yield f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                pass

            # 2) Periodic status snapshot + decision-log tail.
            now = time.monotonic()
            if now - last_flush >= _POLL_INTERVAL:
                last_flush = now
                status = _build_status_payload(project_root)
                yield f"event: status\ndata: {json.dumps(status, ensure_ascii=False)}\n\n"
                if stream_all:
                    entries, cursor = _decision_log_tail(log_path, cursor)
                    if entries:
                        yield (
                            "event: decision-log\n"
                            f"data: {json.dumps(entries, ensure_ascii=False)}\n\n"
                        )
    finally:
        await hub.unsubscribe(queue)


@router.get("/events")
async def stream_events(
    project_root: str = "",
    stream: str = Query("status", description="Stream type: status (default) or all"),
) -> StreamingResponse:
    """SSE stream of real-time status updates.

    Query parameters:
    - ``project_root`` — project root directory (defaults to CWD).
    - ``stream`` — ``status`` (default) for periodic status snapshots, or
      ``all`` which also includes decision-log tail events.

    Live chat / run-state events are always streamed regardless of ``stream``
    (they originate in-process, see design §18).

    The client connects via ``EventSource('/api/v1/events?stream=all')``.
    """
    root = _resolve_project(project_root)
    generator = _event_generator(root, stream == "all")
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
