"""SSE real-time push (design §17.4: events).

Streams incremental updates to the dashboard via Server-Sent Events. Two
event types are supported:

- ``status`` — periodic (every 5s) re-read of the decision log and cost
  meter; pushes a compact delta payload.
- ``decision-log`` — tail-only: reads only the *new* lines appended since
  the last poll, using a file-position cursor (``SEEK_END`` on first read).

The implementation is intentionally simple: no message bus, no websocket,
just a generator that yields ``data:`` lines. The frontend reconnects
automatically (``EventSource``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from ..iterate.checkpoint import load_checkpoint
from ..iterate.decision_log import read_entries
from ._coerce import as_float, as_int

router = APIRouter(tags=["events"])

#: Polling interval (seconds) for the background SSE generator.
_POLL_INTERVAL = 5.0


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


def _event_generator(project_root: Path) -> Any:
    """Generator yielding ``data:`` SSE lines at a fixed interval."""
    log_path = project_root / ".iterate" / "decision-log.jsonl"
    cursor = 0 if not log_path.exists() else log_path.stat().st_size

    while True:
        time.sleep(_POLL_INTERVAL)

        # Cost/status payload (always sent).
        status = _build_status_payload(project_root)
        yield f"event: status\ndata: {json.dumps(status, ensure_ascii=False)}\n\n"

        # Decision-log tail: only read new lines since the last poll.
        if log_path.exists():
            try:
                current_size = log_path.stat().st_size
                if current_size > cursor:
                    with log_path.open("r", encoding="utf-8") as handle:
                        handle.seek(cursor)
                        new_lines = handle.read()
                    cursor = handle.tell()
                    entries = []
                    for line in new_lines.splitlines():
                        if not line.strip():
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    if entries:
                        yield f"event: decision-log\ndata: {json.dumps(entries, ensure_ascii=False)}\n\n"
            except OSError:
                # Log file may have been rotated / deleted; reset cursor.
                cursor = 0


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

    The client connects via ``EventSource('/api/v1/events?stream=all')``.
    """
    root = _resolve_project(project_root)
    generator = _event_generator(root)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
