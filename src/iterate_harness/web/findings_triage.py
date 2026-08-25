"""Persistent findings-triage journal for the WebUI (design §17.3 P2).

The Runs page lets the user triage a finding from a past run — ``approve``
(agree with the finding / accept its suggested fix) or ``reject`` (the
finding is a false positive, its fix should be skipped). Decisions persist
in ``.iterate/findings-triage.jsonl`` so they survive restarts and stay
visible across pages; they are a *human* record that complements the
engine's pause-menu approvals (design §18).

The journal is append-only (mirrors :class:`~iterate_harness.web.security.AuditLog`):
the **latest** decision for a ``(file, line, dimension)`` key wins, so
re-triaging a finding just appends a new line instead of rewriting history.
Writes are best-effort (never raise); reads are defensive (never raise on
corrupt lines).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iterate_harness.utils.file_lock import exclusive_file_lock

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

log = logging.getLogger(__name__)

#: Journal file name (inside the project's ``.iterate`` directory).
TRIAGE_FILE = "findings-triage.jsonl"

#: Separator for the (file, line, dimension) dedup key — chosen so it cannot
#: collide with real file names (``:`` is not a valid path separator in
#: practice but ``:::`` with newline-free keys is unambiguous).
KEY_SEPARATOR = ":::"

#: Allowed decision values.
VALID_DECISIONS = frozenset({"approve", "reject"})


def _key(*, file: Any, line: Any, dimension: Any) -> str:
    """Build the dedup key for a finding (mirrors runs.py findings dedup).

    ``None`` line numbers are canonicalized to the empty string so the key
    matches the frontend ``triageKey`` (which renders a missing line as ``""``).
    """
    line_part = "" if line is None else str(line)
    return f"{file}{KEY_SEPARATOR}{line_part}{KEY_SEPARATOR}{dimension}"


def journal_path(project_root: str | Path) -> Path:
    """Resolve the triage journal path, creating the directory if needed."""
    directory = Path(project_root) / ".iterate"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / TRIAGE_FILE


def load_decisions(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """Return ``key -> latest decision`` for every triaged finding.

    Later lines override earlier ones for the same key (the journal is
    append-only). Returns an empty dict when the journal is missing or
    unreadable.
    """
    path = Path(project_root) / ".iterate" / TRIAGE_FILE
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        key = str(parsed.get("key") or "")
        if not key:
            continue
        decisions[key] = parsed
    return decisions


def list_decisions(project_root: str | Path) -> list[dict[str, Any]]:
    """All triage decisions ordered by most-recently-updated first."""
    decisions = load_decisions(project_root)
    ordered = sorted(decisions.values(), key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return ordered


def record_decision(
    project_root: str | Path,
    *,
    file: Any,
    line: Any,
    dimension: Any,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Append one triage decision; returns the stored record.

    ``decision`` must be ``approve`` or ``reject`` (validated by the route's
    request model; this function is defensive about it). Best-effort write:
    a failing journal append never raises.
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"invalid decision: {decision!r} (allowed: {', '.join(sorted(VALID_DECISIONS))})")
    record: dict[str, Any] = {
        "key": _key(file=file, line=line, dimension=dimension),
        "file": str(file),
        "line": int(line) if line is not None and str(line).lstrip("-").isdigit() else line,
        "dimension": str(dimension),
        "decision": decision,
        "note": (note or "").strip() or None,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    # Hold the journal lock around the append so a concurrent clear's
    # read-modify-rewrite can never drop this record. A failed append is
    # surfaced to the caller (HTTP 500) instead of returning a phantom "ok"
    # record that was never persisted.
    lock_path = journal_path(project_root).with_name(f"{TRIAGE_FILE}.lock")
    try:
        with exclusive_file_lock(lock_path):
            with journal_path(project_root).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        log.error("web findings-triage append failed: %s", exc)
        raise
    return record


def clear_decision(
    project_root: str | Path,
    *,
    file: Any,
    line: Any,
    dimension: Any,
) -> bool:
    """Remove every decision for a finding key (compaction, not an append).

    Rewrites the journal without the matching key. Returns whether any
    decision was removed.
    """
    key = _key(file=file, line=line, dimension=dimension)
    lock_path = journal_path(project_root).with_name(f"{TRIAGE_FILE}.lock")
    with exclusive_file_lock(lock_path):
        decisions = load_decisions(project_root)
        if key not in decisions:
            return False
        decisions.pop(key)
        _rewrite(project_root, decisions)
    return True


def clear_all(project_root: str | Path) -> int:
    """Clear every triage decision; returns the number removed."""
    lock_path = journal_path(project_root).with_name(f"{TRIAGE_FILE}.lock")
    with exclusive_file_lock(lock_path):
        decisions = load_decisions(project_root)
        count = len(decisions)
        if count:
            _rewrite(project_root, {})
    return count


def _rewrite(project_root: str | Path, decisions: dict[str, dict[str, Any]]) -> None:
    """Atomic rewrite of the journal from the given key->record map.

    Writes to a temp file first, then atomically replaces the journal, so an
    interrupted compaction never leaves a truncated/corrupt journal behind.
    A failed rewrite is surfaced to the caller (HTTP 500), never silently
    swallowed.
    """
    path = journal_path(project_root)
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            for record in decisions.values():
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        temp_path.replace(path)
    except OSError:
        log.error("web findings-triage rewrite failed")
        raise
    finally:
        # Never leave a stale temp file behind after a failed compaction.
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("web findings-triage temp cleanup failed: %s", exc)


def triage_state_for(
    decisions: dict[str, dict[str, Any]],
    *,
    file: Any,
    line: Any,
    dimension: Any,
) -> dict[str, Any] | None:
    """Return the latest decision record for a finding, or ``None``."""
    return decisions.get(_key(file=file, line=line, dimension=dimension))


__all__ = [
    "TRIAGE_FILE",
    "VALID_DECISIONS",
    "clear_all",
    "clear_decision",
    "journal_path",
    "list_decisions",
    "load_decisions",
    "record_decision",
    "triage_state_for",
]
