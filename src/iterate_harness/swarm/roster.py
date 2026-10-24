"""One roster across every teammate backend — who is actually running right now.

The engine already runs whole teams: ``subprocess`` teammates (one OS process
each), ``in_process`` teammates (asyncio Tasks in this process), and plain
shell tasks. Every executor keeps a *private* view of what it knows —
``BackgroundTaskManager._tasks``, ``InProcessBackend._active`` — and none of
those views was ever surfaced. ``list_teammates`` / ``get_teammate_status`` /
``active_agents`` / ``shutdown_all`` all existed with zero callers, so a loop
that hangs gave the operator nothing but the whole terminal: no way to see
*which* worker is stuck, how long it has been running, or what it last said,
and no way to stop just that one.

This module is the single aggregation point for all of it.

``build_roster`` is a pure read (no process signalling, no writes).
``stop_agent`` is the one mutating call, and it goes through
``BackgroundTaskManager.stop_task`` so a stop behaves exactly the way the
engine's own stop path behaves — same process-group signal, same in-process
abort, same watcher drain, same completion notifications.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

log = logging.getLogger(__name__)

#: Terminal statuses for a task record.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "killed"})

#: Every status a task record can legitimately hold. Both the CLI and the web
#: route validate ``--status`` / ``?status=`` against this set, so a typo gets
#: the same answer from either entry point instead of one silently returning
#: an empty list.
KNOWN_STATUSES = frozenset({"pending", "running", "completed", "failed", "killed"})

#: Default size of the per-agent output tail shown on the roster. Bounded so
#: a chatty worker can never turn the response into megabytes.
DEFAULT_TAIL_BYTES = 2000

#: Hard cap on the number of agents one roster can carry. The terminal-record
#: eviction in the task manager already bounds the store, so this is a second,
#: cheaper guard for the API/CLI rendering path.
MAX_AGENTS = 200

AgentKind = Literal["subprocess", "in_process", "shell", "other"]
AgentStatus = Literal["pending", "running", "completed", "failed", "killed", "unknown"]


def _task_kind(task_type: str) -> AgentKind:
    """Map a task record's type onto the kind the console displays."""
    if task_type == "in_process_teammate":
        return "in_process"
    if task_type in {"local_agent", "remote_agent"}:
        return "subprocess"
    if task_type == "local_bash":
        return "shell"
    return "other"


def _safe_status(status: str) -> AgentStatus:
    """Normalise a status value; anything unknown degrades to ``unknown``.

    The membership test is what makes the return type honest — a raw ``return
    status`` would hand callers an unvalidated ``str`` where the roster
    advertises a closed set.
    """
    if status in KNOWN_STATUSES:
        return cast(AgentStatus, status)
    return "unknown"


def _utc_iso(value: float | None) -> str | None:
    """Render an epoch timestamp as UTC ISO-8601, or ``None`` when absent."""
    if not isinstance(value, (int, float)) or value == 0:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _output_bytes(output_file: Path) -> int:
    """File size in bytes; ``0`` for a missing or unreadable file."""
    try:
        return max(0, output_file.stat().st_size)
    except OSError:
        return 0


def _task_start(task: Any) -> float:
    """Wall-clock start: ``started_at`` when present, else ``created_at``."""
    for attr in ("started_at", "created_at"):
        value = getattr(task, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 0.0


#: Prefix added to a tail whose head was dropped by the byte cap. The message
#: is emitted *here*, where the cut actually happened, so a caller that passes
#: a large cap never sees a misleading "truncated" marker.
_TRUNCATION_MARKER = "… earlier output truncated ({} bytes)"

#: How many leading lines of a marker-prefixed tail the CLI shows.
_TAIL_PREVIEW_LINES = 8


def _read_output_tail(output_file: Path, max_bytes: int) -> str:
    """Bounded read of the last *max_bytes* of a task output file.

    The tail is read as *bytes*: a text-mode handle rejects a nonzero
    end-relative seek (``can't do nonzero end-relative seeks``), which would
    turn every chatty worker into an empty tail instead of the last screenful.
    Returns ``""`` when there is nothing to show, never raises.
    """
    if max_bytes <= 0:
        return ""
    try:
        size = output_file.stat().st_size
    except OSError:
        return ""
    if size <= 0:
        return ""
    start = 0 if size <= max_bytes else max(0, size - max_bytes)
    try:
        with output_file.open("rb") as handle:
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if start > 0:
        return f"{_TRUNCATION_MARKER.format(size - start)}\n" + text
    return text


def _agent_view_from_task(task: Any, *, tail_bytes: int) -> dict[str, Any] | None:
    """Turn one ``TaskRecord`` into a roster row. Returns ``None`` when unusable."""
    task_id = getattr(task, "id", None)
    if not isinstance(task_id, str) or not task_id:
        return None

    task_type = str(getattr(task, "type", "") or "")
    status = _safe_status(str(getattr(task, "status", "") or ""))
    metadata = getattr(task, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}

    agent_id = str(metadata.get("agent_id") or "")
    team = str(metadata.get("team") or "")

    output_file = getattr(task, "output_file", None)
    tail = ""
    size = 0
    if isinstance(output_file, Path):
        size = _output_bytes(output_file)
        if tail_bytes > 0 and size > 0:
            try:
                tail = _read_output_tail(output_file, tail_bytes)
            except OSError:
                tail = ""

    started = _task_start(task)
    ended = getattr(task, "ended_at", None)
    ended = float(ended) if isinstance(ended, (int, float)) and ended > 0 else None
    running = status == "running"
    if running:
        duration = max(0.0, time.time() - started) if started > 0 else 0.0
    else:
        duration = max(0.0, (ended or started) - started) if started > 0 and ended else 0.0

    progress = metadata.get("progress")
    return {
        "id": task_id,
        "agent_id": agent_id,
        "team": team,
        "kind": _task_kind(task_type),
        "backend": "in_process" if task_type == "in_process_teammate" else "subprocess",
        "task_type": task_type,
        "status": status,
        "running": running,
        "description": str(getattr(task, "description", "") or ""),
        "status_note": str(metadata.get("status_note") or ""),
        "progress": str(progress) if progress is not None else None,
        "started_at": _utc_iso(started) if started > 0 else None,
        "ended_at": _utc_iso(ended),
        "duration_s": round(duration, 1),
        "return_code": getattr(task, "return_code", None),
        "output_bytes": size,
        "tail": tail,
        "cwd": str(getattr(task, "cwd", "") or ""),
    }


def _in_process_duration_hints() -> dict[str, float]:
    """Best-effort ``agent_id`` → started-epoch hints from the in-process backend.

    In-process teammates register a task record with the manager too, so the
    roster never misses them; this only exists to prefer the backend's own
    start time when it is available. Failures are swallowed — the roster must
    never be taken down by a backend that has already been torn down.
    """
    hints: dict[str, float] = {}
    try:
        from iterate_harness.swarm.registry import get_backend_registry

        executor = get_backend_registry().get_executor("in_process")
    except Exception:
        return hints
    list_teammates = getattr(executor, "list_teammates", None)
    if list_teammates is None:
        return hints
    try:
        teammates = list_teammates()
    except Exception:
        return hints
    for entry in teammates or []:
        if not isinstance(entry, tuple) or len(entry) < 3:
            continue
        agent_id, _running, _duration = entry[:3]
        if isinstance(agent_id, str) and isinstance(_duration, (int, float)):
            hints[agent_id] = max(0.0, time.time() - float(_duration))
    return hints


def build_roster(
    *,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    max_agents: int = MAX_AGENTS,
    status: str | None = None,
) -> dict[str, Any]:
    """Return one roster row per known task, enriched with an output tail.

    The roster is built from ``BackgroundTaskManager.list_tasks``, the one
    store every backend writes to (subprocess spawns, shell tasks and
    ``register_in_process_task`` all land there). Rows are sorted running
    first, then longest-running first, so the operator's eyes land on the live
    workers without scanning.

    Args:
        tail_bytes: Bounded size of each row's output tail (``0`` to omit).
        max_agents: Hard cap on the number of rows returned.
        status: Optional status filter (``running`` / ``completed`` / ...).

    Raises: nothing — an unreachable task manager degrades to an empty roster.
    """
    try:
        from iterate_harness.tasks.manager import get_task_manager
        from iterate_harness.tasks.types import TaskStatus

        manager = get_task_manager()
        # An unrecognised filter degrades to "no filter" rather than an empty
        # roster: the route and the CLI already reject a bad status with an
        # error, so a caller that reaches this with a typo should see the team
        # rather than an empty list they would read as "nobody is running".
        selected: str | None = status if status in KNOWN_STATUSES else None
        tasks = list(manager.list_tasks(status=cast(TaskStatus | None, selected)))
    except Exception:
        log.debug("roster: task manager unavailable", exc_info=True)
        tasks = []

    tail_bytes = max(0, int(tail_bytes))
    rows: list[dict[str, Any]] = []
    for task in tasks:
        row = _agent_view_from_task(task, tail_bytes=tail_bytes)
        if row is not None:
            rows.append(row)

    # Prefer the backend's own start time for in-process teammates.
    hints = _in_process_duration_hints() if any(row["agent_id"] for row in rows) else {}
    for row in rows:
        hint = hints.get(row["agent_id"])
        if hint:
            row["started_at"] = _utc_iso(hint)
            if row["running"]:
                row["duration_s"] = round(max(0.0, time.time() - hint), 1)

    def _sort_key(row: dict[str, Any]) -> tuple[int, float]:
        return (0 if row["running"] else 1, -(row["duration_s"] or 0.0))

    rows.sort(key=_sort_key)

    total = len(rows)
    rows = rows[: max(1, min(int(max_agents), MAX_AGENTS))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "truncated": total > len(rows),
        "running": sum(1 for row in rows if row["running"]),
        "failed": sum(1 for row in rows if row["status"] == "failed"),
        "killed": sum(1 for row in rows if row["status"] == "killed"),
        "completed": sum(1 for row in rows if row["status"] == "completed"),
        "agents": rows,
    }


def render_roster_text(view: dict[str, Any], *, max_lines: int = 200) -> str:
    """Render a roster as aligned plain text for the CLI.

    Columns are padded to their widest value so the table survives a
    copy-paste; bounded to *max_lines* so a large team cannot scroll past the
    end of a terminal page.
    """
    agents = view.get("agents")
    agents = agents if isinstance(agents, list) else []
    if not agents:
        return "没有正在运行或记录在册的子代理。"

    columns: list[tuple[str, str]] = [
        ("状态", "status"),
        ("类型", "kind"),
        ("子代理", "agent_id"),
        ("任务", "id"),
        ("时长(s)", "duration_s"),
        ("输出(B)", "output_bytes"),
        ("返回码", "return_code"),
    ]

    def _cell(row: dict[str, Any], key: str) -> str:
        value = row.get(key)
        if value is None or value == "":
            return "-"
        return str(value)

    widths: dict[str, int] = {}
    for _label, key in columns:
        widths[key] = max(max(len(_cell(row, key)) for row in agents), len(_label))

    # No rstrip: the last column's width comes from whichever is wider — the
    # header label or the widest cell. Stripping the padding would make the
    # header and the rows different lengths and break copy-paste alignment.
    header = "  ".join(label.ljust(widths[key]) for label, key in columns)
    lines = [header]
    for row in agents:
        lines.append("  ".join(_cell(row, key).ljust(widths[key]) for _label, key in columns))

    out = list(lines[:max_lines])
    if len(lines) > max_lines:
        out.append(f"…（{len(lines) - max_lines} 行已截断）")
    return "\n".join(out)


async def stop_agent(agent_id: str) -> dict[str, Any]:
    """Stop one agent by task id or swarm identity (``name@team``).

    Search order matters: a task id wins, then the swarm ``agent_id`` from
    record metadata. Anything not found returns ``{"status": "not_found"}``
    instead of raising — the caller is an operator clicking a button, and a
    clear message beats a traceback.
    """
    key = (agent_id or "").strip()
    if not key:
        return {"status": "invalid", "message": "缺少子代理标识"}

    try:
        from iterate_harness.tasks.manager import get_task_manager

        manager = get_task_manager()
    except Exception as exc:
        return {
            "status": "error",
            "message": f"任务管理器不可用：{type(exc).__name__}: {exc}",
        }

    record = manager.get_task(key)
    resolved_id = key
    if record is None:
        for candidate in manager.list_tasks():
            metadata = getattr(candidate, "metadata", None)
            if isinstance(metadata, dict) and metadata.get("agent_id") == key:
                record = candidate
                resolved_id = candidate.id
                break

    if record is None:
        return {
            "status": "not_found",
            "message": f"未找到子代理 {key}（它可能已经退出并被回收）",
        }
    if record.status in _TERMINAL_STATUSES:
        return {
            "status": "already_terminal",
            "id": resolved_id,
            "status_now": record.status,
            "message": f"子代理 {resolved_id} 已处于 {record.status}",
        }

    try:
        updated = await manager.stop_task(resolved_id)
    except ValueError as exc:
        # A task with no live process and no cancellation handle cannot be
        # stopped at all. That is a stale record (usually left behind by an
        # earlier run), not a stop that failed mid-flight — naming it
        # "already_terminal" would paper over the record, so it is reported
        # as its own outcome with a reason.
        current = manager.get_task(resolved_id)
        if current is not None and current.status in _TERMINAL_STATUSES:
            return {
                "status": "already_terminal",
                "id": resolved_id,
                "status_now": current.status,
                "message": f"子代理 {resolved_id} 已处于 {current.status}",
            }
        return {
            "status": "error",
            "id": resolved_id,
            "message": f"子代理 {resolved_id} 没有可停止的进程或取消句柄（多半是上一轮遗留的旧记录）：{exc}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "id": resolved_id,
            "message": f"停止 {resolved_id} 失败：{type(exc).__name__}: {exc}",
        }
    return {
        "status": "stopped",
        "id": updated.id,
        "agent_id": str((getattr(updated, "metadata", None) or {}).get("agent_id") or ""),
        "status_now": updated.status,
        "return_code": int(updated.return_code)
        if isinstance(updated.return_code, int)
        else None,
        "message": f"已停止子代理 {updated.id}",
    }


def roster_as_json(view: dict[str, Any]) -> str:
    """Serialise a roster for ``--json`` output."""
    return json.dumps(view, ensure_ascii=False, indent=2)


__all__ = [
    "DEFAULT_TAIL_BYTES",
    "KNOWN_STATUSES",
    "MAX_AGENTS",
    "build_roster",
    "render_roster_text",
    "roster_as_json",
    "stop_agent",
]
