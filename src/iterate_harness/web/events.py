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
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import StreamingResponse

from ..iterate.checkpoint import load_checkpoint
from ..iterate.decision_log import read_entries
from ._coerce import as_finite, as_float, as_int
from .hub import hub
from .security import allowed_roots, root_is_allowed, default_root

router = APIRouter(tags=["events"])

#: Polling interval (seconds) for the background SSE generator.
_POLL_INTERVAL = 5.0

#: Wake-up cadence (seconds) while draining hub events, so the periodic
#: status snapshot is never starved by a chat-heavy burst.
_HUB_WAKEUP = 0.5


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else (default_root() or Path.cwd())
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    # ``project_root`` is caller-controlled, so containment inside the root is
    # not enough on its own: without this the parameter *selects* the root, and
    # a valid token granted read/write over any directory on the machine.
    if not root_is_allowed(root):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Project root is outside the roots this WebUI serves: {root}. "
                f"Allowed: {', '.join(sorted(allowed_roots())) or '(none)'}"
            ),
        )
    return root


#: How long (seconds) a status snapshot stays cached. Multiple SSE
#: connections (one per open WebUI tab) otherwise each re-read the whole
#: decision log on every poll — O(connections) full journal reads per 5s.
_STATUS_CACHE_TTL = 2.0
_status_cache_lock = threading.Lock()
_status_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def _build_status_payload(project_root: Path) -> dict[str, Any]:
    """Build a compact status snapshot for the SSE stream.

    The decision log is re-read on every call unless a recent snapshot for the
    same project is still cached — a run loop appends to it, so always-fresh
    reads would be wasteful across concurrent SSE connections.
    """
    now = time.monotonic()
    with _status_cache_lock:
        cached = _status_cache.get(project_root)
        if cached is not None and now - cached[0] < _STATUS_CACHE_TTL:
            return cached[1]
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
    # ``or`` keeps the "report missing/present-but-zero → use checkpoint"
    # fallback, but a NaN cost is truthy and would otherwise win; sanitize
    # non-finite values to 0.0 so the SSE payload stays valid JSON.
    cost_usd = as_float(report_data.get("totalCostUsd")) or as_float(
        checkpoint.get("cost_usd", 0.0)
    )
    payload = {
        "entryCount": len(entries),
        "latestRound": latest_round,
        "checkpointExists": bool(checkpoint),
        "checkpointRound": as_int(checkpoint.get("round")) if checkpoint else 0,
        "totalTokens": as_int(report_data.get("totalTokens")) or as_int(checkpoint.get("input_tokens", 0)),
        "totalCostUsd": as_finite(cost_usd),
        "converged": converged,
        "timestamp": time.time(),
    }
    with _status_cache_lock:
        # Keep the cache from growing without bound across projects/sessions.
        if len(_status_cache) > 64:
            oldest = min(_status_cache, key=lambda key: _status_cache[key][0])
            _status_cache.pop(oldest, None)
        _status_cache[project_root] = (time.monotonic(), payload)
    return payload


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
            if not new_lines:
                return [], cursor
            if new_lines.endswith("\n"):
                new_cursor = handle.tell()
            else:
                # No trailing newline: the last line may still be being
                # written. Do NOT park the cursor past it — a JSONDecodeError
                # on a half-written record would otherwise permanently skip
                # the rest of that line. Keep the cursor at the start of the
                # unterminated line so the next poll re-reads it once it has
                # completed.
                last_nl = new_lines.rfind("\n")
                if last_nl == -1:
                    return [], cursor
                new_cursor = cursor + last_nl + 1
                new_lines = new_lines[: last_nl + 1]
                handle.seek(new_cursor)
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


def _sse_frame(event_type: str, data: Any, event_id: int | None = None) -> str:
    """Render one Server-Sent Event frame.

    ``id:`` is emitted for every event so a reconnecting ``EventSource`` sends
    the last id it saw back in ``Last-Event-ID``. Ids come from the hub's
    global monotonic counter (hub events carry the id assigned at publish
    time; locally generated frames allocate the next id), so the id sequence
    is meaningful across reconnects and the stream can replay missed events
    or explicitly announce a gap (see :func:`_event_generator`). The frame
    order (``id`` → ``event`` → ``data``) matches the SSE spec.
    """
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


async def _event_generator(
    project_root: Path, stream_all: bool, last_event_id: int | None = None
) -> Any:
    """Async generator interleaving hub events with periodic file snapshots.

    Every emitted frame carries a hub-global monotonic ``id:``. When the
    client reconnects with a ``Last-Event-ID``, the events published after
    that id are replayed from the hub's bounded ring buffer; when the id is
    too old to replay (evicted) an explicit ``sse-gap`` notice frame is
    emitted first so the client knows it must re-sync via REST instead of
    assuming a continuous stream. Note that frames from *other* sources
    (periodic status snapshots, decision-log tails) interleave with hub
    events, so a duplicate replay after an out-of-order id is possible —
    chat entries carry unique ids and run-state/progress payloads are
    idempotent, which makes duplicates harmless.
    """
    log_path = project_root / ".iterate" / "decision-log.jsonl"
    try:
        cursor = 0 if not log_path.exists() else log_path.stat().st_size
    except OSError:
        # The journal may have been deleted/rotated between the exists() and
        # stat() calls (or mid-stream by a concurrent cleanup); re-anchor at 0
        # rather than letting the whole SSE stream die with an OSError.
        cursor = 0
    # Scoped subscription: this generator reports one project, so it must only
    # ever see that project's live events. An unscoped subscription made a tab
    # watching project B mirror project A's run state and chat.
    queue = await hub.subscribe(str(project_root))
    last_flush = 0.0
    try:
        if last_event_id is not None:
            missed, complete = hub.resume_after(last_event_id, str(project_root))
            if not complete:
                yield _sse_frame(
                    "sse-gap",
                    {
                        "reason": "unresumable",
                        "lastEventId": last_event_id,
                        "message": (
                            "断线重连：上次位置之后的部分事件已被淘汰，无法完整回放；"
                            "请以 REST 状态为准重新同步。"
                        ),
                    },
                    hub.next_event_id(),
                )
            for missed_event in missed:
                yield _sse_frame(missed_event.type, missed_event.data, missed_event.id)
        while True:
            # 1) Drain live hub events (chat / run-state / progress) promptly.
            #    No ``continue`` here: the snapshot below is time-gated so a
            #    steady event stream can never starve the status push.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HUB_WAKEUP)
                yield _sse_frame(event.type, event.data, event.id)
            except asyncio.TimeoutError:
                pass

            # 2) Periodic status snapshot + decision-log tail. Both read the
            #    whole journal and parse it, so they run on a worker thread: a
            #    big decision log made this generator block the event loop,
            #    which froze *every* connection's live events during the read.
            now = time.monotonic()
            if now - last_flush >= _POLL_INTERVAL:
                last_flush = now
                status = await asyncio.to_thread(_build_status_payload, project_root)
                yield _sse_frame("status", status, hub.next_event_id())
                if stream_all:
                    entries, cursor = await asyncio.to_thread(
                        _decision_log_tail, log_path, cursor
                    )
                    if entries:
                        yield _sse_frame(
                            "decision-log", entries, hub.next_event_id()
                        )
    finally:
        await hub.unsubscribe(queue)


@router.get("/events")
async def stream_events(
    request: Request,
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

    The standard ``Last-Event-ID`` header (sent by a reconnecting
    ``EventSource``) is honoured: still-buffered events published after that
    id are replayed; when the id is too old to replay the stream starts from
    the current state and opens with an explicit ``sse-gap`` notice so the
    client knows to re-sync.

    The client connects via ``EventSource('/api/v1/events?stream=all')``.
    """
    root = _resolve_project(project_root)
    # EventSource sends this automatically on reconnect. Only a plain
    # non-negative integer is honoured; anything else starts a fresh stream
    # (no gap notice — a first-time connect has no Last-Event-ID at all).
    raw_last_id = request.headers.get("last-event-id", "").strip()
    last_event_id: int | None = None
    if raw_last_id.isdigit():
        last_event_id = int(raw_last_id)
    generator = _event_generator(root, stream == "all", last_event_id)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
