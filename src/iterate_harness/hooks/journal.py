"""Hook-execution journal (read + write) shared by the CLI and the console.

The engine has always executed lifecycle hooks (``session_start``,
``pre_tool_use``, ``post_tool_use``, …) but the result of every execution was
discarded: a hook that rejected a tool call degraded to a ``blocked`` flag the
operator could only observe as a mystery refusal inside a run, and a hook that
silently failed (missing command, timeout, a model call that never answered)
was visible only as a log line. There was no surface that answered "did my
hook fire, did it pass, and why not?".

This module is the journal both surfaces share:

- :func:`record_hook_execution` appends one best-effort line to
  ``.iterate/hook-journal.jsonl`` (never raises, never blocks the caller);
- :func:`iter_hook_executions` streams the whole journal defensively (a
  corrupt line is skipped — a trail that cannot be read must not take the
  console down);
- :func:`list_hook_executions` filters (event / hook type / success), pages
  newest-first, and reports the matching total;
- :func:`distinct_events` / :func:`distinct_hook_types` power the page's
  filter dropdowns.

The *write* side is driven by :class:`~iterate_harness.hooks.executor.HookExecutor`
so every hook — command, http, prompt or agent — lands in the same journal with
the same fields, and the CLI and the console can never disagree about what ran.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iterate_harness.utils.file_lock import exclusive_file_lock

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

#: Hook journal file name (inside the project's ``.iterate`` directory).
JOURNAL_DIR = ".iterate"
JOURNAL_FILE = "hook-journal.jsonl"

#: Journal rotation cap (bytes). Beyond this the journal is trimmed to the
#: newest lines under the lock, so a chatty hook cannot grow the trail forever.
MAX_JOURNAL_BYTES = 5 * 1024 * 1024

#: Bounded size of the ``detail`` / ``reason`` / ``output`` fields recorded
#: into the journal (matches the executor's own output bound).
_MAX_FIELD_CHARS = 8192

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _bounded(value: Any, limit: int = _MAX_FIELD_CHARS) -> str:
    """Coerce *value* to a bounded string (defensive against huge payloads)."""
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[truncated, {omitted} chars omitted]..."


def journal_path(project_root: str | Path) -> Path:
    """Resolve the hook journal path for a project root."""
    return Path(project_root) / JOURNAL_DIR / JOURNAL_FILE


def record_hook_execution(
    project_root: str | Path,
    *,
    event: str,
    hook_type: str,
    matcher: str = "",
    detail: str = "",
    model: str = "",
    success: bool,
    blocked: bool = False,
    reason: str = "",
    output: str = "",
    duration_ms: int = 0,
    returncode: int | None = None,
    status_code: int | None = None,
    source: str = "settings",
) -> dict[str, Any]:
    """Append one hook execution to the journal (best-effort, never raises).

    The journal directory is created lazily only on write — reading an empty
    project must not touch the disk. A failing append is a warning, not a
    crash: hook execution happens inline in front of a tool call or turn
    boundary, and the hook result must reach the caller even when the journal
    is on a read-only mount or mid-rotation.
    """
    record: dict[str, Any] = {
        "timestamp": _now_iso(),
        "event": str(event),
        "hook_type": str(hook_type),
        "matcher": _bounded(matcher),
        "detail": _bounded(detail),
        "model": _bounded(model),
        "success": bool(success),
        "blocked": bool(blocked),
        "reason": _bounded(reason),
        "output": _bounded(output),
        "duration_ms": int(duration_ms),
        "source": str(source),
    }
    if returncode is not None:
        record["returncode"] = int(returncode)
    if status_code is not None:
        record["status_code"] = int(status_code)
    path = journal_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("cannot create hook journal dir %s: %s", path.parent, exc)
        return record
    line = json.dumps(record, ensure_ascii=False)
    try:
        with exclusive_file_lock(Path(str(path) + ".lock")):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            # Rotate lazily after *every* append so the trail stays bounded even
            # for a chatty hook; only ever trims to the newest lines. (The
            # previous `if not existed` guard only rotated the very first write
            # into a fresh journal, so a long-lived project could grow the file
            # without bound; the 5 MiB cap was never enforced after that first
            # line.)
            _trim_journal(path)
    except OSError as exc:
        log.warning("cannot append hook journal %s: %s", path, exc)
    return record


def _trim_journal(path: Path) -> None:
    """Trim the journal to ``MAX_JOURNAL_BYTES``, keeping the newest lines."""
    try:
        if path.stat().st_size <= MAX_JOURNAL_BYTES:
            return
    except OSError:
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    # Keep the newest lines that fit under the cap (always keep at least one).
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        line_bytes = len(line.encode("utf-8")) + 1
        if kept and size + line_bytes > MAX_JOURNAL_BYTES:
            break
        kept.append(line)
        size += line_bytes
    kept.reverse()
    # Replace atomically: truncating the live file would let a concurrent
    # reader (or a crash mid-write) observe a half-written journal, and a
    # failed write would destroy the whole trail. A sibling temp file + rename
    # means readers always see either the old file or the complete new one.
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def iter_hook_executions(project_root: str | Path) -> Iterator[dict[str, Any]]:
    """Yield every hook execution in the journal (oldest first, defensive).

    Corrupt lines and non-dict lines are skipped — a single mangled line must
    never blank the whole view nor hide the healthy lines around it. A missing
    or unreadable journal yields nothing.
    """
    path = journal_path(project_root)
    try:
        if not path.is_file():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                yield parsed
    except OSError:
        # A journal we cannot read is a warning, not a crash: the trail may be
        # on a read-only mount or mid-rotation. Return nothing rather than
        # raising into a 500 on a page that exists to *show* history.
        return


def _matches(
    entry: dict[str, Any],
    *,
    event: str,
    hook_type: str,
    success: bool | None,
) -> bool:
    """Apply the CLI/route filters to one journal entry (pure)."""
    if event:
        if str(entry.get("event") or "") != event:
            return False
    if hook_type:
        if str(entry.get("hook_type") or "") != hook_type:
            return False
    if success is not None:
        if bool(entry.get("success")) is not success:
            return False
    return True


def list_hook_executions(
    project_root: str | Path,
    *,
    event: str = "",
    hook_type: str = "",
    success: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(page, total)`` of matching executions, newest first.

    A missing journal is an empty page with ``total`` 0 — never an error.
    """
    matched: list[dict[str, Any]] = []
    for entry in iter_hook_executions(project_root):
        if _matches(entry, event=event, hook_type=hook_type, success=success):
            matched.append(entry)
    total = len(matched)
    # Newest first: the console's job is "what ran most recently".
    matched.reverse()
    page = matched[offset : offset + limit]
    return page, total


def distinct_events(project_root: str | Path) -> list[str]:
    """Every distinct event seen in the journal (sorted)."""
    seen: set[str] = set()
    for entry in iter_hook_executions(project_root):
        value = str(entry.get("event") or "")
        if value:
            seen.add(value)
    return sorted(seen)


def distinct_hook_types(project_root: str | Path) -> list[str]:
    """Every distinct hook type seen in the journal (sorted)."""
    seen: set[str] = set()
    for entry in iter_hook_executions(project_root):
        value = str(entry.get("hook_type") or "")
        if value:
            seen.add(value)
    return sorted(seen)


def journal_summary(project_root: str | Path) -> dict[str, Any]:
    """Aggregate the journal for the inventory surface (cheap, single pass)."""
    total = 0
    successes = 0
    blocked = 0
    by_event: dict[str, int] = {}
    for entry in iter_hook_executions(project_root):
        total += 1
        if bool(entry.get("success")):
            successes += 1
        if bool(entry.get("blocked")):
            blocked += 1
        event = str(entry.get("event") or "")
        if event:
            by_event[event] = by_event.get(event, 0) + 1
    return {
        "total": total,
        "successes": successes,
        "failures": total - successes,
        "blocked": blocked,
        "by_event": dict(sorted(by_event.items())),
    }


def render_hook_log_table(page: list[dict[str, Any]], total: int, limit: int, offset: int) -> str:
    """Render the execution page as an aligned human-readable table."""
    lines: list[str] = []
    if not page:
        lines.append("No hook executions recorded (the engine has not run a hook for this project).")
        return "\n".join(lines)
    widths = {"time": 24, "event": 14, "type": 8, "result": 8}
    header = (
        f"{'time':<{widths['time']}} {'event':<{widths['event']}} "
        f"{'type':<{widths['type']}} {'result':<{widths['result']}} detail"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for entry in page:
        ts = str(entry.get("timestamp") or "")
        # Trim to "YYYY-MM-DD HH:MM:SS" for readability; keep timezone in json.
        display_ts = ts[:19] if len(ts) >= 19 else ts
        event = str(entry.get("event") or "")
        hook_type = str(entry.get("hook_type") or "")
        if bool(entry.get("success")):
            result = "ok"
        elif bool(entry.get("blocked")):
            result = "blocked"
        else:
            result = "failed"
        detail = str(entry.get("detail") or "")
        if len(detail) > 60:
            detail = detail[:57] + "..."
        lines.append(
            f"{display_ts:<{widths['time']}} {event:<{widths['event']}} "
            f"{hook_type:<{widths['type']}} {result:<{widths['result']}} {detail}"
        )
    lines.append("")
    lines.append(f"Showing {len(page)} of {total} matching executions (offset {offset}, limit {limit}).")
    return "\n".join(lines)


def render_hook_log_markdown(page: list[dict[str, Any]], total: int, limit: int, offset: int) -> str:
    """Render the execution page as a Markdown digest (for PR / incident docs)."""
    lines = [
        "# iterate-harness hook executions",
        "",
        f"Showing {len(page)} of {total} matching executions (offset {offset}, limit {limit}).",
        "",
    ]
    if not page:
        lines.append("_No hook executions recorded._")
        return "\n".join(lines)
    lines.append("| time | event | type | result | detail |")
    lines.append("|---|---|---|---|---|")
    for entry in page:
        ts = str(entry.get("timestamp") or "")
        event = str(entry.get("event") or "")
        hook_type = str(entry.get("hook_type") or "")
        if bool(entry.get("success")):
            result = "ok"
        elif bool(entry.get("blocked")):
            result = "blocked"
        else:
            result = "failed"
        detail = str(entry.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        if len(detail) > 100:
            detail = detail[:97] + "..."
        lines.append(f"| {ts} | {event} | {hook_type} | {result} | {detail} |")
    return "\n".join(lines)


def hook_log_csv(page: list[dict[str, Any]], total: int, limit: int, offset: int) -> str:
    """Render the execution page as a CSV payload (one row per execution)."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "timestamp",
            "event",
            "hook_type",
            "matcher",
            "detail",
            "model",
            "success",
            "blocked",
            "reason",
            "duration_ms",
            "returncode",
            "status_code",
            "source",
        ]
    )
    for entry in page:
        writer.writerow(
            [
                entry.get("timestamp", ""),
                entry.get("event", ""),
                entry.get("hook_type", ""),
                entry.get("matcher", ""),
                entry.get("detail", ""),
                entry.get("model", ""),
                "true" if bool(entry.get("success")) else "false",
                "true" if bool(entry.get("blocked")) else "false",
                entry.get("reason", ""),
                entry.get("duration_ms", ""),
                entry.get("returncode", ""),
                entry.get("status_code", ""),
                entry.get("source", ""),
            ]
        )
    return buffer.getvalue()


def hook_execution_time_ms(started: float) -> int:
    """Return the elapsed milliseconds since *started* (monotonic)."""
    return int((time.monotonic() - started) * 1000)


def hook_export_filename(fmt: str, *, now: datetime | None = None) -> str:
    """Server-side filename for a hook-journal export (``md`` / ``csv`` / ``json``)."""
    stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%d-%H%M%S")
    suffix = "md" if fmt == "md" else fmt
    return f"iterate-hooks-{stamp}.{suffix}"
