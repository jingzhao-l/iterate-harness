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
from datetime import UTC, datetime
from pathlib import Path

from .types import DecisionLogEntry

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
        entries.append(
            DecisionLogEntry(
                timestamp=str(parsed.get("timestamp", "")),
                round=int(parsed.get("round", 0)),
                type=str(parsed.get("type", "")),
                data=dict(parsed.get("data") or {}),
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
