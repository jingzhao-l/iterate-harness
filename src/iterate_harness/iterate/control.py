"""Cross-process control channel for a running iterate loop.

The console (``ih iterate run`` / ``ih iterate review``) runs headless: once it
starts, the only intervention the operator has is ``Ctrl-C``, which tears the
process down mid-write and discards the round it was working on. The WebUI has
pause / stop / nudge for the runs *it* started, but those controls live in the
server process's memory and cannot reach a loop started in another terminal.

This module is the shared channel on disk. A headless run publishes a small
``run-control.json`` state file plus an append-only ``run-control.jsonl``
journal under ``.iterate/``. A second terminal appends commands with
``ih iterate control nudge|pause|stop``; the loop's policy polls the journal at
each turn/round boundary and applies them through the same paths the WebUI
uses (``inject_nudge`` / a graceful stop that keeps the checkpoint).

Design rules:

- **The run owns consumption.** Only the process that wrote the state file
  consumes commands; the cursor (``run-control.cursor``) is therefore written
  by one process and never contended.
- **Commands are append-only.** A command is never rewritten or deleted once
  written (except by ``clear``), so the journal is an audit trail of operator
  interventions even after the run ends.
- **Best-effort and fail-open.** A missing/malformed journal yields "no
  commands" — a broken control file must never break a review loop.
- **No active run, no command.** Appending a command without a live run is
  refused with an executable remedy instead of writing into a journal nobody
  will read.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from iterate_harness.iterate.run_lock import pid_is_live, read_lease
from iterate_harness.utils.file_lock import exclusive_file_lock, sidecar_lock_path
from iterate_harness.utils.fs import atomic_write_text

log = logging.getLogger(__name__)

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

CONTROL_DIR = ".iterate"
STATE_FILE = "run-control.json"
JOURNAL_FILE = "run-control.jsonl"
CURSOR_FILE = "run-control.cursor"

#: Command kinds. ``pause`` and ``stop`` both end the loop gracefully at the
#: next round boundary; they differ in the operator-facing reason (a pause
#: says the checkpoint can be resumed). Headless runs have no interactive
#: pause menu, so there is nothing to wait for — the honest behaviour is a
#: clean stop with the checkpoint intact.
ControlKind = Literal["nudge", "pause", "stop"]
CONTROL_KINDS: tuple[str, ...] = ("nudge", "pause", "stop")

#: Upper bound on a nudge / stop reason. A control file is not a prompt
#: transport: 4k characters is already far more than an operator needs and
#: keeps one JSONL line well inside an atomic append.
MAX_CONTENT_CHARS = 4000


class ControlChannelError(RuntimeError):
    """A control command cannot be written, carrying an executable remedy."""

    def __init__(self, code: str, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.remedy = remedy


@dataclass(frozen=True)
class ControlCommand:
    """One operator command read from the journal."""

    id: str
    kind: str
    content: str
    created_at: float
    line_no: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "createdAt": self.created_at,
            "line": self.line_no,
        }


def _paths(project_root: str | Path) -> tuple[Path, Path, Path, Path]:
    directory = Path(project_root) / CONTROL_DIR
    return (
        directory,
        directory / STATE_FILE,
        directory / JOURNAL_FILE,
        directory / CURSOR_FILE,
    )


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def read_run_state(project_root: str | Path) -> dict[str, Any] | None:
    """Read the published run state (``None`` when absent / malformed)."""
    _, state_path, _, _ = _paths(project_root)
    return _read_json(state_path)


def begin_run(project_root: str | Path, *, role: str = "console") -> str:
    """Publish a fresh control state for this process and clear stale commands.

    Called by a headless run before the engine is constructed. Returns the
    run id written to the state file; :func:`end_run` uses it to avoid
    clobbering a newer run's state.
    """
    directory, state_path, journal_path, cursor_path = _paths(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    now = time.time()
    payload = {
        "run_id": run_id,
        "pid": os.getpid(),
        "role": role,
        "status": "running",
        "started_at": now,
        "started_at_iso": _iso(now),
    }
    atomic_write_text(
        state_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        mode=0o600,
    )
    # A previous run's unconsumed commands must not leak into this one.
    for stale in (journal_path, cursor_path):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("iterate control: could not clear %s: %s", stale, exc)
    return run_id


def end_run(project_root: str | Path, run_id: str) -> None:
    """Mark the run finished (only when ``run_id`` still owns the state)."""
    _, state_path, _, _ = _paths(project_root)
    state = _read_json(state_path)
    if state is None or str(state.get("run_id") or "") != run_id:
        return
    state = dict(state)
    state["status"] = "finished"
    state["finished_at"] = time.time()
    state["finished_at_iso"] = _iso(state["finished_at"])
    try:
        atomic_write_text(
            state_path,
            json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as exc:
        log.warning("iterate control: could not mark run finished: %s", exc)


def _run_is_active(state: dict[str, Any] | None) -> bool:
    if not state or str(state.get("status") or "") != "running":
        return False
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    return pid_is_live(pid)


def active_run_state(project_root: str | Path) -> dict[str, Any] | None:
    """Return the state only when it names a live run, else ``None``."""
    state = read_run_state(project_root)
    return state if _run_is_active(state) else None


def local_run_channel(project_root: str | Path) -> "ControlChannel | None":
    """A channel for *this* process's run, or ``None`` when it is not the owner.

    The engine's default policy calls this at construction time. A TUI/WebUI
    session that never published a control state gets ``None`` and therefore
    no polling at all — the channel is strictly additive.
    """
    state = active_run_state(project_root)
    if state is None:
        return None
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if pid != os.getpid():
        return None
    return ControlChannel(project_root)


def _read_cursor(cursor_path: Path) -> tuple[int, str | None]:
    """Return ``(consumed_line_count, last_consumed_command_id)``.

    The id is what lets :func:`read_pending` tell "the journal grew" from "the
    journal was replaced by a different one of the same length" — a bare line
    count silently skipped the new commands in that case.
    """
    try:
        raw = cursor_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError, OSError, UnicodeDecodeError):
        return 0, None
    if not raw:
        return 0, None
    # Legacy plain-integer cursor (no id) stays readable.
    try:
        return max(0, int(raw)), None
    except ValueError:
        pass
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0, None
    if not isinstance(payload, dict):
        return 0, None
    try:
        line = max(0, int(payload.get("line") or 0))
    except (TypeError, ValueError):
        line = 0
    last_id = payload.get("lastId")
    return line, str(last_id) if last_id else None


def _write_cursor(cursor_path: Path, line: int, last_id: str | None) -> None:
    payload = {"line": max(0, line), "lastId": last_id}
    atomic_write_text(
        cursor_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        mode=0o600,
    )


def _parse_command(line: str, line_no: int) -> ControlCommand | None:
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("kind") or "")
    command_id = str(payload.get("id") or "")
    if kind not in CONTROL_KINDS or not command_id:
        return None
    content = payload.get("content")
    if not isinstance(content, str):
        content = ""
    try:
        created_at = float(payload.get("created_at") or 0.0)
    except (TypeError, ValueError):
        created_at = 0.0
    return ControlCommand(
        id=command_id,
        kind=kind,
        content=content,
        created_at=created_at,
        line_no=line_no,
    )


def read_pending(project_root: str | Path) -> list[ControlCommand]:
    """Read commands the run has not consumed yet (never mutates state)."""
    _, _, journal_path, cursor_path = _paths(project_root)
    try:
        raw = journal_path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return []
    except (OSError, UnicodeDecodeError):
        return []
    cursor, last_id = _read_cursor(cursor_path)
    lines = raw.splitlines()
    # A journal shorter than the cursor was truncated/replaced: treat it as a
    # fresh journal rather than silently skipping every line.
    if cursor > len(lines):
        cursor = 0
    elif cursor > 0 and last_id is not None:
        # Same length, different content: the line the cursor pointed at is no
        # longer the command it consumed, so this is a new journal.
        previous = _parse_command(lines[cursor - 1], cursor) if cursor <= len(lines) else None
        if previous is None or previous.id != last_id:
            cursor = 0
    pending: list[ControlCommand] = []
    seen: set[str] = set()
    for index, line in enumerate(lines[cursor:], start=cursor + 1):
        if not line.strip():
            continue
        command = _parse_command(line, index)
        if command is None or command.id in seen:
            continue
        seen.add(command.id)
        pending.append(command)
    return pending


def consume_commands(project_root: str | Path, commands: list[ControlCommand]) -> None:
    """Advance the run's cursor past ``commands`` (idempotent, monotonic)."""
    if not commands:
        return
    _, _, journal_path, cursor_path = _paths(project_root)
    lock_path = sidecar_lock_path(journal_path)
    try:
        with exclusive_file_lock(lock_path):
            current, current_id = _read_cursor(cursor_path)
            newest = max(commands, key=lambda command: command.line_no)
            if newest.line_no >= current:
                _write_cursor(cursor_path, newest.line_no, newest.id)
            else:
                _write_cursor(cursor_path, current, current_id)
    except OSError as exc:
        # The next poll will see the commands again; the policy dedupes by id,
        # so a failed cursor write cannot double-apply a nudge.
        log.warning("iterate control: could not advance cursor: %s", exc)


def _append_line(journal_path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        journal_path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def append_command(
    project_root: str | Path,
    kind: str,
    content: str = "",
) -> ControlCommand:
    """Append one command for the live run.

    Raises :class:`ControlChannelError` when no run is active, the kind is
    unknown, or a nudge carries no text.
    """
    normalized = (kind or "").strip().lower()
    if normalized not in CONTROL_KINDS:
        raise ControlChannelError(
            "invalid_kind",
            f"Unknown control command {kind!r}; expected one of {', '.join(CONTROL_KINDS)}",
            f"Use `ih iterate control {CONTROL_KINDS[0]} <text>` or "
            f"`ih iterate control stop`.",
        )
    text = (content or "").strip()
    if normalized == "nudge" and not text:
        raise ControlChannelError(
            "empty_nudge",
            "A nudge needs instruction text",
            'Example: ih iterate control nudge "focus the next round on auth"',
        )
    if len(text) > MAX_CONTENT_CHARS:
        raise ControlChannelError(
            "too_long",
            f"Control text is {len(text)} characters; the limit is {MAX_CONTENT_CHARS}",
            "Shorten the instruction — this channel is for one operator note, not a prompt.",
        )
    if active_run_state(project_root) is None:
        raise ControlChannelError(
            "no_active_run",
            "No iterate run is active in this project",
            "Start one with `ih iterate run` (or `ih iterate review`), then send the "
            "command from another terminal.",
        )
    directory, _, journal_path, _ = _paths(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    now = time.time()
    payload = {
        "id": command_id,
        "kind": normalized,
        "content": text,
        "created_at": now,
        "created_at_iso": _iso(now),
    }
    with exclusive_file_lock(sidecar_lock_path(journal_path)):
        _append_line(journal_path, payload)
    # The line number is only known after the append; re-read so the caller
    # gets a command that can be passed straight to ``consume_commands``.
    for command in read_pending(project_root):
        if command.id == command_id:
            return command
    # The command was written but is already past this process's cursor (a
    # run consumed it in the tiny window). Return a synthetic record.
    return ControlCommand(
        id=command_id,
        kind=normalized,
        content=text,
        created_at=now,
        line_no=0,
    )


def clear_pending(project_root: str | Path) -> int:
    """Drop every unconsumed command; returns how many were dropped.

    Used to undo a mistaken nudge before the loop reaches its next boundary.
    The journal is truncated (not appended to) so the cursor stays coherent;
    the run's state file is untouched.
    """
    pending = read_pending(project_root)
    if not pending:
        return 0
    _, _, journal_path, cursor_path = _paths(project_root)
    with exclusive_file_lock(sidecar_lock_path(journal_path)):
        atomic_write_text(journal_path, "", mode=0o600)
        _write_cursor(cursor_path, 0, None)
    return len(pending)


def _lease_is_live(lease: Any) -> bool:
    """True when a lease names a run that is still alive (best effort)."""
    if lease is None:
        return False
    try:
        import socket

        if str(getattr(lease, "host", "")) != socket.gethostname():
            # Another host: a pid means nothing here. The lease's own staleness
            # policy (age) is the only answer we can give without mutating it.
            return True
        return pid_is_live(int(getattr(lease, "pid", 0) or 0))
    except (TypeError, ValueError):
        return False


def run_status(project_root: str | Path) -> dict[str, Any]:
    """Read-only status payload shared by ``ih iterate control status`` / JSON.

    Never mutates anything (not even a stale lease): a status command must be
    safe to run from monitoring. ``active`` means "a run this channel can
    control"; ``leaseActive`` also covers a TUI/WebUI run that holds the run
    lease but never published a control state.
    """
    state = read_run_state(project_root)
    active = _run_is_active(state)
    lease = read_lease(project_root)
    lease_active = _lease_is_live(lease)
    pending = read_pending(project_root)
    checkpoint = None
    try:
        from .checkpoint import load_checkpoint

        checkpoint = load_checkpoint(project_root)
    except Exception:  # noqa: BLE001 - status must never fail on a bad checkpoint
        checkpoint = None
    last_entry: dict[str, Any] | None = None
    try:
        from .decision_log import read_entries

        entries = read_entries(project_root)
        if entries:
            entry = entries[-1]
            last_entry = {
                "timestamp": entry.timestamp,
                "round": entry.round,
                "type": entry.type,
            }
    except Exception:  # noqa: BLE001 - a broken ledger must not hide run state
        last_entry = None
    return {
        "active": active,
        "controllable": active,
        "leaseActive": lease_active,
        "stale": bool(state and not active and state.get("status") == "running"),
        "run": state,
        "lease": (
            {
                "holder": lease.holder,
                "role": lease.role,
                "pid": lease.pid,
                "host": lease.host,
                "acquiredAt": lease.acquired_at,
            }
            if lease is not None
            else None
        ),
        "pending": [command.to_dict() for command in pending],
        "pendingCount": len(pending),
        "checkpoint": checkpoint,
        "lastEntry": last_entry,
    }


class ControlChannel:
    """Polling view over one run's journal (constructed by the loop policy)."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)

    def poll(self) -> list[ControlCommand]:
        return read_pending(self.project_root)

    def consume(self, commands: list[ControlCommand]) -> None:
        consume_commands(self.project_root, commands)


__all__ = [
    "CONTROL_DIR",
    "CONTROL_KINDS",
    "CURSOR_FILE",
    "JOURNAL_FILE",
    "MAX_CONTENT_CHARS",
    "STATE_FILE",
    "ControlChannel",
    "ControlChannelError",
    "ControlCommand",
    "active_run_state",
    "append_command",
    "begin_run",
    "clear_pending",
    "consume_commands",
    "end_run",
    "local_run_channel",
    "read_pending",
    "read_run_state",
    "run_status",
]
