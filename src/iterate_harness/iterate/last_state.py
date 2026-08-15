"""Last-run summary for the iterate resume screen (TUI startup).

When the React TUI boots in a project with iterate history, the backend
reads ``.iterate/decision-log.jsonl`` and builds a compact summary of the
last finished loop (verdict, mode, rounds, findings) plus the last Esc
intervention — enough context for the user to decide whether to resume
via ``/iterate resume`` without re-reading the whole log.

All parsing is defensive: a missing or malformed log yields ``None``.
"""

from __future__ import annotations

from typing import Any

from .decision_log import DecisionLogEntry, read_entries

#: How many finding summaries to preview in the resume panel.
MAX_PREVIEW_FINDINGS = 3

_SEVERITY_KEYS = ("critical", "high", "medium", "low")


def summarize_last_run(project_root: str) -> dict[str, Any] | None:
    """Summarize the last finished iterate run; ``None`` when no history."""
    entries = read_entries(project_root)
    if not entries:
        return None

    report = _last_entry(entries, "report")
    if report is None:
        return None

    severity_counts = {key: 0 for key in _SEVERITY_KEYS}
    findings = _findings_of(report)
    for finding in findings:
        key = str(finding.get("severity") or "").strip().lower()
        if key in severity_counts:
            severity_counts[key] += 1

    max_round = max((entry.round for entry in entries), default=0)
    intervention = _last_intervention(entries)
    return {
        "timestamp": report.timestamp,
        "mode": str(report.data.get("mode") or "dry-run"),
        "verdict": str(report.data.get("verdict") or "unknown"),
        "rounds": max(max_round, report.round),
        "totalFindings": len(findings),
        "severity": severity_counts,
        "preview": [
            {
                "severity": str(f.get("severity") or "?"),
                "file": str(f.get("file") or "?"),
                "dimension": str(f.get("dimension") or "?"),
                "summary": str(f.get("summary") or "")[:120],
            }
            for f in findings[:MAX_PREVIEW_FINDINGS]
        ],
        "lastIntervention": intervention,
        "entryCount": len(entries),
    }


def _last_entry(entries: list[DecisionLogEntry], entry_type: str) -> DecisionLogEntry | None:
    for entry in reversed(entries):
        if entry.type == entry_type:
            return entry
    return None


def _findings_of(entry: DecisionLogEntry) -> list[dict[str, Any]]:
    raw = entry.data.get("findings") if isinstance(entry.data, dict) else None
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict)]


def _last_intervention(entries: list[DecisionLogEntry]) -> dict[str, Any] | None:
    for entry in reversed(entries):
        if entry.type != "decision":
            continue
        data = entry.data if isinstance(entry.data, dict) else {}
        if data.get("kind") == "intervention":
            return {
                "timestamp": entry.timestamp,
                "round": entry.round,
                "action": str(data.get("action") or ""),
                "detail": str(data.get("detail") or ""),
            }
    return None


__all__ = ["MAX_PREVIEW_FINDINGS", "summarize_last_run"]
