"""Defensive-kernel journal (read + write) shared by the CLI and the console.

The defensive kernel guarantees that code-mode edits are atomic and that the
project invariants hold after every mutation. When a guarantee fires, the
engine rolls the edit back and tells the *model* why -- but the operator is
left with nothing: the rollback exists only as a log line and as a tool error
inside a run that has already scrolled away. There was no surface that
answered "did the guard actually run, what did it check, and why was that edit
rolled back?".

This module is the journal both surfaces share:

- :func:`record_defense_event` appends one best-effort line to
  ``.iterate/defense-journal.jsonl`` (never raises, never blocks the caller);
- :func:`iter_defense_events` streams the whole journal defensively (a corrupt
  line is skipped -- a trail that cannot be read must not take the console
  down);
- :func:`list_defense_events` filters (kind / outcome / tool), pages
  newest-first, and reports the matching total;
- :func:`defense_journal_summary` aggregates the trail for the page header;
- renderers produce the same table / Markdown / CSV the other diagnostic
  journals use.

The *write* side is driven by
:class:`~iterate_harness.defensive.kernel.DefensiveKernel` (and by
``ih defense check`` for a manual gate run), so every verdict -- automatic or
manual -- lands in the same journal with the same fields, and the CLI and the
console can never disagree about what happened.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from iterate_harness.utils.file_lock import exclusive_file_lock

#: Python 3.10 compatibility -- ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

#: Journal file name (inside the project's ``.iterate`` directory).
JOURNAL_DIR = ".iterate"
JOURNAL_FILE = "defense-journal.jsonl"

#: Journal rotation cap (bytes). Beyond this the journal is trimmed to the
#: newest lines under the lock, so a long-running session cannot grow the trail
#: forever.
MAX_JOURNAL_BYTES = 5 * 1024 * 1024

#: Bounded size of the free-text fields recorded into the journal.
_MAX_FIELD_CHARS = 8192

#: Bounded number of violations kept per event (the page only needs the first
#: few to explain a rollback; a pathological config must not bloat a line).
MAX_VIOLATIONS = 20

#: Event kinds. ``rollback`` covers an edit that was reverted (failed tool,
#: failed/unmeasured guard); ``invariant`` is a guard verdict that allowed the
#: edit to commit; ``manual-check`` is an operator-run gate from the CLI.
KIND_ROLLBACK = "rollback"
KIND_INVARIANT = "invariant"
KIND_MANUAL_CHECK = "manual-check"
KINDS: frozenset[str] = frozenset((KIND_ROLLBACK, KIND_INVARIANT, KIND_MANUAL_CHECK))

#: Outcomes. ``pass`` / ``fail`` / ``error`` mirror the invariant-report
#: contract; ``unmeasured`` means the guard ran zero checks (a configuration
#: problem, never a pass); ``failed_tool`` is a rollback caused by the tool
#: itself raising/returning an error rather than by a violated invariant.
OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_ERROR = "error"
OUTCOME_UNMEASURED = "unmeasured"
OUTCOME_FAILED_TOOL = "failed_tool"
OUTCOMES: frozenset[str] = frozenset(
    (OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_ERROR, OUTCOME_UNMEASURED, OUTCOME_FAILED_TOOL)
)

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
    """Resolve the defensive journal path for a project root."""
    return Path(project_root) / JOURNAL_DIR / JOURNAL_FILE


def _normalize_violations(violations: Sequence[Any] | None) -> list[dict[str, str]]:
    """Project invariant violations onto the bounded journal shape."""
    normalized: list[dict[str, str]] = []
    for violation in list(violations or [])[:MAX_VIOLATIONS]:
        if isinstance(violation, dict):
            kind = str(violation.get("kind") or "")
            label = str(violation.get("label") or "")
            detail = str(violation.get("detail") or "")
        else:
            kind = str(getattr(violation, "kind", "") or "")
            label = str(getattr(violation, "label", "") or "")
            detail = str(getattr(violation, "detail", "") or "")
        normalized.append(
            {
                "kind": _bounded(kind, 64),
                "label": _bounded(label, 256),
                "detail": _bounded(detail, 1000),
            }
        )
    return normalized


def record_defense_event(
    project_root: str | Path,
    *,
    kind: str,
    outcome: str,
    tool: str = "",
    path: str = "",
    trigger: str = "",
    reason: str = "",
    checks_run: int = 0,
    duration_ms: int = 0,
    rolled_back: bool = False,
    violations: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Append one defensive-kernel event to the journal (best-effort).

    The journal directory is created lazily only on write -- reading an empty
    project must not touch the disk. A failing append is a warning, not a crash:
    this runs inline in the mutation path, and a journal on a read-only mount
    must never stop an edit from being rolled back or committed.
    """
    record: dict[str, Any] = {
        "id": f"df_{uuid4().hex[:12]}",
        "timestamp": _now_iso(),
        "kind": str(kind),
        "outcome": str(outcome),
        "tool": _bounded(tool, 128),
        "path": _bounded(path, 1024),
        "trigger": _bounded(trigger, 64),
        "reason": _bounded(reason),
        "checks_run": max(0, int(checks_run)),
        "duration_ms": max(0, int(duration_ms)),
        "rolled_back": bool(rolled_back),
        "violations": _normalize_violations(violations),
    }
    if record["kind"] not in KINDS:
        # A kind we do not know is still evidence; keep it rather than losing
        # the line, but it will not match the page's known filters.
        record["kind"] = _bounded(record["kind"], 64)
    if record["outcome"] not in OUTCOMES:
        record["outcome"] = _bounded(record["outcome"], 64)

    file_path = journal_path(project_root)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("cannot create defense journal dir %s: %s", file_path.parent, exc)
        return record
    line = json.dumps(record, ensure_ascii=False)
    try:
        with exclusive_file_lock(Path(str(file_path) + ".lock")):
            with file_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            _trim_journal(file_path)
    except OSError as exc:
        log.warning("cannot append defense journal %s: %s", file_path, exc)
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
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        line_bytes = len(line.encode("utf-8")) + 1
        if kept and size + line_bytes > MAX_JOURNAL_BYTES:
            break
        kept.append(line)
        size += line_bytes
    kept.reverse()
    # Replace atomically: readers always see either the old complete file or
    # the new complete file, never a half-written journal.
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def iter_defense_events(project_root: str | Path) -> Iterator[dict[str, Any]]:
    """Yield every journal event (oldest first, defensive).

    Corrupt lines and non-dict lines are skipped -- a single mangled line must
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
        # on a read-only mount or mid-rotation.
        return


def _matches(
    entry: dict[str, Any],
    *,
    kind: str,
    outcome: str,
    tool: str,
) -> bool:
    """Apply the CLI/route filters to one journal entry (pure)."""
    if kind:
        if str(entry.get("kind") or "") != kind:
            return False
    if outcome:
        if str(entry.get("outcome") or "") != outcome:
            return False
    if tool:
        if str(entry.get("tool") or "") != tool:
            return False
    return True


def list_defense_events(
    project_root: str | Path,
    *,
    kind: str = "",
    outcome: str = "",
    tool: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(page, total)`` of matching events, newest first.

    A missing journal is an empty page with ``total`` 0 -- never an error.
    """
    matched: list[dict[str, Any]] = []
    for entry in iter_defense_events(project_root):
        if _matches(entry, kind=kind, outcome=outcome, tool=tool):
            matched.append(entry)
    total = len(matched)
    matched.reverse()
    page = matched[offset : offset + limit]
    return page, total


def distinct_kinds(project_root: str | Path) -> list[str]:
    """Every distinct event kind seen in the journal (sorted)."""
    seen: set[str] = set()
    for entry in iter_defense_events(project_root):
        value = str(entry.get("kind") or "")
        if value:
            seen.add(value)
    return sorted(seen)


def distinct_outcomes(project_root: str | Path) -> list[str]:
    """Every distinct outcome seen in the journal (sorted)."""
    seen: set[str] = set()
    for entry in iter_defense_events(project_root):
        value = str(entry.get("outcome") or "")
        if value:
            seen.add(value)
    return sorted(seen)


def distinct_tools(project_root: str | Path) -> list[str]:
    """Every distinct tool name seen in the journal (sorted)."""
    seen: set[str] = set()
    for entry in iter_defense_events(project_root):
        value = str(entry.get("tool") or "")
        if value:
            seen.add(value)
    return sorted(seen)


def defense_journal_summary(project_root: str | Path) -> dict[str, Any]:
    """Aggregate the journal for the inventory surface (cheap, single pass)."""
    total = 0
    rollbacks = 0
    invariant_runs = 0
    manual_checks = 0
    last_verdict = ""
    last_event_at = ""
    by_kind: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    for entry in iter_defense_events(project_root):
        total += 1
        kind = str(entry.get("kind") or "")
        outcome = str(entry.get("outcome") or "")
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if outcome:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        if kind == KIND_ROLLBACK:
            rollbacks += 1
        elif kind == KIND_MANUAL_CHECK:
            manual_checks += 1
        if kind in (KIND_INVARIANT, KIND_ROLLBACK, KIND_MANUAL_CHECK):
            invariant_runs += 1
            last_verdict = outcome
        timestamp = str(entry.get("timestamp") or "")
        if timestamp >= last_event_at:
            last_event_at = timestamp
    return {
        "total": total,
        "rollbacks": rollbacks,
        "invariant_runs": invariant_runs,
        "manual_checks": manual_checks,
        "last_verdict": last_verdict,
        "last_event_at": last_event_at,
        "by_kind": dict(sorted(by_kind.items())),
        "by_outcome": dict(sorted(by_outcome.items())),
    }


def _result_label(entry: dict[str, Any]) -> str:
    """Short human result for one event."""
    if bool(entry.get("rolled_back")):
        return f"rolled back ({entry.get('outcome') or 'fail'})"
    outcome = str(entry.get("outcome") or "?")
    if outcome == OUTCOME_UNMEASURED:
        return "unmeasured"
    return outcome


def render_defense_log_table(
    page: list[dict[str, Any]], total: int, limit: int, offset: int
) -> str:
    """Render the event page as an aligned human-readable table."""
    if not page:
        return "No defensive-kernel events recorded for this project."
    widths = {"time": 19, "kind": 13, "result": 22, "tool": 14}
    header = (
        f"{'time':<{widths['time']}} {'kind':<{widths['kind']}} "
        f"{'result':<{widths['result']}} {'tool':<{widths['tool']}} checks  detail"
    )
    lines = [header, "-" * len(header)]
    for entry in page:
        ts = str(entry.get("timestamp") or "")
        display_ts = ts[:19].replace("T", " ") if len(ts) >= 19 else ts
        kind = str(entry.get("kind") or "")
        tool = str(entry.get("tool") or "-") or "-"
        path = str(entry.get("path") or "")
        reason = str(entry.get("reason") or "")
        detail = f"{path} {reason}".strip()
        if len(detail) > 80:
            detail = detail[:77] + "..."
        lines.append(
            f"{display_ts:<{widths['time']}} {kind:<{widths['kind']}} "
            f"{_result_label(entry):<{widths['result']}} {tool:<{widths['tool']}} "
            f"{int(entry.get('checks_run') or 0):<7} {detail}"
        )
    lines.append("")
    lines.append(
        f"Showing {len(page)} of {total} matching events (offset {offset}, limit {limit})."
    )
    return "\n".join(lines)


def render_defense_log_markdown(
    page: list[dict[str, Any]], total: int, limit: int, offset: int
) -> str:
    """Render the event page as a Markdown digest (for PR / incident docs)."""
    lines = [
        "# iterate-harness defensive kernel events",
        "",
        f"Showing {len(page)} of {total} matching events (offset {offset}, limit {limit}).",
        "",
    ]
    if not page:
        lines.append("_No defensive-kernel events recorded._")
        return "\n".join(lines)
    lines.append("| time | kind | result | tool | path | checks | reason |")
    lines.append("|---|---|---|---|---|---|---|")
    for entry in page:
        reason = str(entry.get("reason") or "").replace("|", "\\|").replace("\n", " ")
        if len(reason) > 160:
            reason = reason[:157] + "..."
        path = str(entry.get("path") or "").replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(entry.get("timestamp") or ""),
                    str(entry.get("kind") or ""),
                    _result_label(entry),
                    str(entry.get("tool") or ""),
                    path,
                    str(int(entry.get("checks_run") or 0)),
                    reason,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def defense_log_csv(
    page: list[dict[str, Any]], total: int, limit: int, offset: int
) -> str:
    """Render the event page as a CSV payload (one row per event)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "timestamp",
            "id",
            "kind",
            "outcome",
            "rolled_back",
            "tool",
            "path",
            "trigger",
            "checks_run",
            "duration_ms",
            "violations",
            "reason",
        ]
    )
    for entry in page:
        violations = entry.get("violations") or []
        summary = "; ".join(
            f"{item.get('kind', '')}:{item.get('label', '')} ({item.get('detail', '')})"
            for item in violations
            if isinstance(item, dict)
        )
        writer.writerow(
            [
                entry.get("timestamp", ""),
                entry.get("id", ""),
                entry.get("kind", ""),
                entry.get("outcome", ""),
                "true" if bool(entry.get("rolled_back")) else "false",
                entry.get("tool", ""),
                entry.get("path", ""),
                entry.get("trigger", ""),
                entry.get("checks_run", 0),
                entry.get("duration_ms", 0),
                summary,
                entry.get("reason", ""),
            ]
        )
    return buffer.getvalue()


def journal_export_filename(fmt: str, now: datetime | None = None) -> str:
    """Stable, server-owned export filename (mirrors the other journals)."""
    stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%d-%H%M%S")
    suffix = "md" if fmt == "md" else fmt
    return f"iterate-defense-{stamp}.{suffix}"


def event_age_seconds(entry: dict[str, Any]) -> float | None:
    """Age of an event in seconds (None when the timestamp is unreadable)."""
    raw = str(entry.get("timestamp") or "")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, time.time() - parsed.timestamp())


__all__ = [
    "JOURNAL_DIR",
    "JOURNAL_FILE",
    "MAX_JOURNAL_BYTES",
    "KIND_ROLLBACK",
    "KIND_INVARIANT",
    "KIND_MANUAL_CHECK",
    "KINDS",
    "OUTCOME_PASS",
    "OUTCOME_FAIL",
    "OUTCOME_ERROR",
    "OUTCOME_UNMEASURED",
    "OUTCOME_FAILED_TOOL",
    "OUTCOMES",
    "journal_path",
    "record_defense_event",
    "iter_defense_events",
    "list_defense_events",
    "distinct_kinds",
    "distinct_outcomes",
    "distinct_tools",
    "defense_journal_summary",
    "render_defense_log_table",
    "render_defense_log_markdown",
    "defense_log_csv",
    "journal_export_filename",
    "event_age_seconds",
]
