"""Append-only decision log for the iterate loop.

Python port of ``harness/iterate-plugin/src/tools/decision-log.ts`` (the
pure core; harness tool registration wires this into the kernel separately).

The log is stored in ``.iterate/decision-log.jsonl`` at the project root
and persists across sessions. Writes are append-only; reads return every
entry in order. Malformed/corrupt files are handled defensively: reads
return what parsed, never raise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import DecisionLogEntry as DecisionLogEntry

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

LOG_DIR = ".iterate"
LOG_FILE = "decision-log.jsonl"

VALID_ENTRY_TYPES = frozenset({
    "round_start",
    "review_result",
    "atomic_fix",
    "architectural_fix",
    "revert",
    "validation",
    "decision",
    "report",
})


def log_path(project_root: str | Path) -> Path:
    """Resolve the log file path, creating the directory if needed."""
    directory = Path(project_root) / LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / LOG_FILE


def append_entry(
    project_root: str | Path, entry: DecisionLogEntry
) -> tuple[int, Path]:
    """Append one entry to the decision log (JSONL format).

    Returns ``(entry_count_after_append, log_file_path)``.
    """
    file_path = log_path(project_root)
    line = json.dumps(
        {
            "timestamp": entry.timestamp,
            "round": entry.round,
            "type": entry.type,
            "data": entry.data,
        },
        ensure_ascii=False,
    )
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    count = len(read_entries(project_root))
    return count, file_path


def read_entries(project_root: str | Path) -> list[DecisionLogEntry]:
    """Read all entries from the decision log.

    Returns ``[]`` when the log is missing, unreadable, or corrupt.
    Individual malformed lines are skipped (the append-only contract makes
    a partial log more valuable than a hard failure).
    """
    file_path = Path(project_root) / LOG_DIR / LOG_FILE
    if not file_path.exists():
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[DecisionLogEntry] = []
    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        try:
            timestamp = str(parsed.get("timestamp", ""))
            round_val = int(parsed.get("round", 0))
            entry_type = str(parsed.get("type", ""))
            data = dict(parsed.get("data") or {})
        except (ValueError, TypeError):
            continue
        entries.append(
            DecisionLogEntry(
                timestamp=timestamp,
                round=round_val,
                type=entry_type,
                data=data,
            )
        )
    return entries


def make_entry(
    *,
    entry_type: str,
    round_number: int,
    data: dict[str, object] | None = None,
) -> DecisionLogEntry:
    """Build a timestamped entry for the current instant (UTC, ISO-8601)."""
    return DecisionLogEntry(
        timestamp=datetime.now(UTC).isoformat(),
        round=round_number,
        type=entry_type,
        data=data or {},
    )


#: Keys that may carry finding payloads across the legacy report shapes.
#: The canonical loop writes the full list as ``findings``, but several older
#: producer paths recorded the same data under different keys (``topFindings``,
#: ``notableFindings``) or nested it under a ``summary`` sub-object. Order here
#: is precedence: the fully-qualified names win over the trimmed slices.
_FINDING_CARRIER_KEYS = ("findings", "notableFindings", "topFindings")


def _normalize_finding(raw: Any) -> dict[str, Any] | None:
    """Return a finding dict when ``raw`` is a dict with the core fields.

    Finding payloads across legacy shapes share the same per-finding keys
    (``dimension``/``file``/``severity``/``summary``); anything else is skipped
    so a mixed list never poisons the report.
    """
    if not isinstance(raw, dict):
        return None
    if not any(key in raw for key in ("dimension", "file", "summary")):
        return None
    return raw


def findings_from_report(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the finding list from a ``report`` entry payload regardless of
    which legacy shape the producer wrote.

    Supports all three historical layouts so the WebUI last-run summary and
    ``ih iterate report`` produce identical, non-empty results:

    - canonical: ``{"findings": [{...}]}``
    - trimmed:   ``{"topFindings": [{...}]}`` or ``{"notableFindings": [...]}``
    - nested:    ``{"summary": {"findings": [...]}}``
    """
    if not isinstance(data, dict):
        return []

    # Nested ``summary`` sub-object may carry the full list itself.
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in _FINDING_CARRIER_KEYS:
            raw = summary.get(key)
            if isinstance(raw, list):
                findings = [f for f in raw if isinstance(f, dict)]
                if findings:
                    return findings

    for key in _FINDING_CARRIER_KEYS:
        raw = data.get(key)
        if isinstance(raw, list):
            findings = [f for f in raw if isinstance(f, dict)]
            if findings:
                return findings

    return []
