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

from .checkpoint import load_checkpoint
from .decision_log import DecisionLogEntry, findings_from_report, read_entries

#: How many finding summaries to preview in the resume panel.
MAX_PREVIEW_FINDINGS = 3

_SEVERITY_KEYS = ("critical", "high", "medium", "low")


def summarize_last_run(project_root: str) -> dict[str, Any] | None:
    """Summarize the last iterate run; ``None`` when no history.

    Prefers a final ``report`` entry (finished run). When the run was
    interrupted/failed before a report landed, falls back to the persisted
    convergence checkpoint so ``/iterate resume`` can continue from the
    last successful convergence point.
    """
    entries = read_entries(project_root)
    if not entries and load_checkpoint(project_root) is None:
        return None

    report = _last_entry(entries, "report")
    if report is not None:
        return _summarize_report(entries, report)

    checkpoint = load_checkpoint(project_root)
    if checkpoint is not None:
        return _summarize_checkpoint(entries, checkpoint)

    return None


def _summarize_report(entries: list[DecisionLogEntry], report: DecisionLogEntry) -> dict[str, Any]:
    severity_counts = {key: 0 for key in _SEVERITY_KEYS}
    findings = _findings_of(report)
    for finding in findings:
        key = str(finding.get("severity") or "").strip().lower()
        if key in severity_counts:
            severity_counts[key] += 1

    max_round = max((entry.round for entry in entries), default=0)
    intervention = _last_intervention(entries)
    # The recovered finding list may be a trimmed/legacy slice (e.g. the
    # ``notableFindings`` top-N), so prefer an explicit count from the entry
    # or its nested ``summary`` when present, and only fall back to the length
    # of the recovered list.
    data = report.data if isinstance(report.data, dict) else {}
    summary = data.get("summary")
    total = data.get("totalFindings")
    if not isinstance(total, int) and isinstance(summary, dict):
        total = summary.get("totalFindings")
    if not isinstance(total, int):
        total = len(findings)
    return {
        "timestamp": report.timestamp,
        "mode": str(data.get("mode") or "dry-run"),
        "verdict": str(data.get("verdict") or "unknown"),
        "rounds": max(max_round, report.round),
        "totalFindings": total,
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


def _summarize_checkpoint(
    entries: list[DecisionLogEntry], checkpoint: dict[str, Any]
) -> dict[str, Any]:
    """Build an "interrupted" summary from the persisted checkpoint."""
    per_dimension = checkpoint.get("per_dimension")
    if not isinstance(per_dimension, dict):
        per_dimension = {}
    severity_counts = {key: 0 for key in _SEVERITY_KEYS}
    # Preview the most recent review_result findings when available so the
    # resume panel still shows concrete findings for an interrupted run.
    preview: list[dict[str, Any]] = []
    for entry in reversed(entries):
        if entry.type != "review_result":
            continue
        findings = _findings_of(entry)
        for finding in findings:
            severity = str(finding.get("severity") or "?")
            if severity in severity_counts:
                severity_counts[severity] += 1
            preview.append(
                {
                    "severity": severity,
                    "file": str(finding.get("file") or "?"),
                    "dimension": str(finding.get("dimension") or "?"),
                    "summary": str(finding.get("summary") or "")[:120],
                }
            )
            if len(preview) >= MAX_PREVIEW_FINDINGS:
                break
        if len(preview) >= MAX_PREVIEW_FINDINGS:
            break
    return {
        "timestamp": str(checkpoint.get("timestamp") or ""),
        "mode": str(checkpoint.get("mode") or "dry-run"),
        "verdict": "interrupted",
        "rounds": int(checkpoint.get("round") or 0),
        "totalFindings": int(checkpoint.get("total_findings") or 0),
        "severity": severity_counts,
        "perDimension": {
            str(key): int(value) for key, value in per_dimension.items()
        },
        "preview": preview,
        "lastIntervention": _last_intervention(entries),
        "entryCount": len(entries),
        "interrupted": True,
    }


def _last_entry(entries: list[DecisionLogEntry], entry_type: str) -> DecisionLogEntry | None:
    for entry in reversed(entries):
        if entry.type == entry_type:
            return entry
    return None


def _findings_of(entry: DecisionLogEntry) -> list[dict[str, Any]]:
    # Model-driven loops may record the full ``findings`` list or only a
    # trimmed/legacy slice (``topFindings`` / ``notableFindings`` / nested
    # ``summary``) — delegate to the shared consumer so the resume panel and
    # the WebUI last-run summary stay populated for every historical shape.
    return findings_from_report(entry.data if isinstance(entry.data, dict) else None)


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
