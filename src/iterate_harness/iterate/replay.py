"""Decision-log replay: chronological narration of an iterate run.

`ih iterate log --replay` re-plays `.iterate/decision-log.jsonl` in
chronological order with relative timestamps (``[+12.3s]``), a round marker,
and a one-line human summary per entry — enough to reconstruct how the loop
unfolded (review → fix → validate → report, including interventions and
reverts) without re-reading raw JSON.

The summarizer is pure and defensive: unknown entry types or malformed
payloads degrade to a truncated JSON preview instead of raising.
"""

from __future__ import annotations

from datetime import datetime

from .decision_log import DecisionLogEntry

#: Truncation width for one-line summaries.
MAX_SUMMARY_WIDTH = 140

#: Per-entry type, the data keys probed (in order) for the narration.
_SUMMARY_KEYS: dict[str, tuple[str, ...]] = {
    "round_start": ("goal", "dimensions", "mode"),
    "review_result": ("newFindings", "totalFindings", "summary"),
    "atomic_fix": ("summary", "description", "file"),
    "architectural_fix": ("summary", "description", "file"),
    "revert": ("reason", "summary", "file"),
    "validation": ("command", "status", "exitCode"),
    "decision": ("action", "kind", "detail"),
    "report": ("verdict", "totalFindings"),
}


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; ``None`` when unparseable."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _truncate(text: str, width: int = MAX_SUMMARY_WIDTH) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= width else cleaned[: width - 1] + "…"


def _summarize_data(entry: DecisionLogEntry) -> str:
    """One-line summary of an entry's data payload."""
    data = entry.data if isinstance(entry.data, dict) else {}
    for key in _SUMMARY_KEYS.get(entry.type, ()):
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            return _truncate(f"{key}={value}")
        text = str(value).strip()
        if text:
            return _truncate(f"{key}={text}")
    if data:
        return _truncate(str(data))
    return "(no payload)"


def build_replay_lines(entries: list[DecisionLogEntry]) -> list[str]:
    """Render the log chronologically with relative offsets from the first entry."""
    if not entries:
        return ["(decision log is empty — nothing to replay)"]

    origin = None
    for entry in entries:
        origin = _parse_timestamp(entry.timestamp)
        if origin is not None:
            break

    lines: list[str] = []
    for entry in entries:
        parsed = _parse_timestamp(entry.timestamp)
        if origin is not None and parsed is not None and parsed >= origin:
            offset = parsed - origin
            total = int(offset.total_seconds())
            marker = f"[+{total}s]" if total > 0 else "[+0s]"
            stamp = entry.timestamp[:19]
        else:
            marker = "[+?s]"
            stamp = entry.timestamp[:19] or "?"
        summary = _summarize_data(entry)
        lines.append(f"{marker} r{entry.round} {entry.type:<16} {summary}   ({stamp})")
    lines.append(f"({len(entries)} entries replayed)")
    return lines


def render_replay(entries: list[DecisionLogEntry]) -> str:
    """Full replay as one printable block."""
    return "\n".join(build_replay_lines(entries))


__all__ = [
    "MAX_SUMMARY_WIDTH",
    "build_replay_lines",
    "render_replay",
]
