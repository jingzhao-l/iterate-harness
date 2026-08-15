"""CI consumption of the final iterate report (decision-log based).

The canonical iterate loops append exactly one ``report`` entry to the
decision log when they finish (see :mod:`.prompts`). This module turns that
machine-readable entry into CI-friendly outputs:

- :func:`render_github` — GitHub Actions workflow commands (``::error ...``)
  so findings land as PR annotations;
- :func:`render_text` — a human summary for local/other-CI use;
- :func:`severity_gate` — an exit-code policy (``--fail-on high`` etc.).

All functions are pure and defensive: a malformed or missing report degrades
to "no findings recorded" instead of crashing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .decision_log import DecisionLogEntry

#: Map finding severity → GitHub annotation level.
GITHUB_ANNOTATION_LEVELS: dict[str, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "notice",
}

#: Severity ranking for the exit-code gate (index = rank, higher = worse).
SEVERITY_ORDER = ("none", "low", "medium", "high", "critical")

DEFAULT_FAIL_ON = "high"


@dataclass
class ReportSummary:
    """Defensive view over one report entry's data payload."""

    verdict: str = "unknown"
    total_findings: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "dry-run"

    @classmethod
    def from_entry(cls, entry: DecisionLogEntry | None) -> ReportSummary:
        data = entry.data if isinstance(entry, DecisionLogEntry) else {}
        if isinstance(data, dict):
            raw_findings = data.get("findings")
            findings = [f for f in raw_findings if isinstance(f, dict)] if isinstance(raw_findings, list) else []
            return cls(
                verdict=str(data.get("verdict") or "unknown"),
                total_findings=_int_or(data.get("totalFindings"), len(findings)),
                findings=findings,
                mode=str(data.get("mode") or "dry-run"),
            )
        return cls()


def _int_or(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) else fallback


def latest_report_entry(entries: list[DecisionLogEntry]) -> DecisionLogEntry | None:
    """Return the last ``report`` entry carrying a findings list, if any."""
    for entry in reversed(entries):
        if entry.type == "report" and isinstance(entry.data, dict) and "findings" in entry.data:
            return entry
    return None


def _escape_workflow_data(text: str) -> str:
    """Escape a workflow-command data segment per the GitHub Actions spec."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_workflow_property(text: str) -> str:
    """Escape a workflow-command property value (no colon escaping needed)."""
    return (
        text.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _annotation_level(severity: Any) -> str:
    key = str(severity or "").strip().lower()
    return GITHUB_ANNOTATION_LEVELS.get(key, "notice")


def render_github(summary: ReportSummary) -> str:
    """Render the report as GitHub Actions workflow commands (one per line)."""
    lines = [
        "::notice::"
        + _escape_workflow_data(
            f"iterate report ({summary.mode}): {summary.total_findings} finding(s), verdict={summary.verdict}"
        )
    ]
    for finding in summary.findings:
        level = _annotation_level(finding.get("severity"))
        properties: list[str] = []
        file_value = finding.get("file")
        if isinstance(file_value, str) and file_value:
            properties.append(f"file={_escape_workflow_property(file_value)}")
        line_value = finding.get("line")
        if isinstance(line_value, int) and line_value > 0:
            properties.append(f"line={line_value}")
        suffix = f" {' '.join(properties)}" if properties else ""
        message = _escape_workflow_data(
            f"[{finding.get('severity') or 'notice'!s}] "
            f"{finding.get('dimension') or 'general'!s}: {finding.get('summary') or ''!s}".strip()
        )
        lines.append(f"::{level}{suffix}::{message}")
    return "\n".join(lines)


def render_text(summary: ReportSummary, gate: dict[str, Any] | None = None) -> str:
    """Render the report as a plain-text summary.

    ``gate`` is the optional ``thresholdGate`` block from the report entry:
    when present its status line (and violations) are appended so CI logs
    explain WHY the exit code failed the threshold gate.
    """
    head = (
        f"iterate report ({summary.mode}): {summary.total_findings} finding(s), "
        f"verdict={summary.verdict}"
    )
    lines = [head]
    if gate is not None:
        lines.append(_render_gate_lines(gate))
    if not summary.findings:
        return "\n".join(lines)
    rows = []
    for finding in summary.findings:
        location = str(finding.get("file") or "(no file)")
        line_value = finding.get("line")
        if isinstance(line_value, int) and line_value > 0:
            location += f":{line_value}"
        rows.append(
            f"  [{finding.get('severity') or '?'!s}] {location} "
            f"{finding.get('dimension') or 'general'!s}: {finding.get('summary') or ''}".rstrip()
        )
    return "\n".join([*lines, *rows])


def _render_gate_lines(gate: dict[str, Any]) -> str:
    """One-line threshold-gate status (violations inlined, capped at 5)."""
    passed = bool(gate.get("passed", True))
    status = "PASS" if passed else "FAIL"
    violations_raw = gate.get("violations")
    violations = [v for v in violations_raw if isinstance(v, dict)] if isinstance(violations_raw, list) else []
    if not violations:
        return f"threshold gate: {status}"
    rendered = [
        f"{v.get('scope')}:{v.get('metric')} {v.get('actual')}>{v.get('limit')}"
        for v in violations[:5]
    ]
    more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
    return f"threshold gate: {status} — {'; '.join(rendered)}{more}"


def threshold_gate(entry: DecisionLogEntry | None) -> dict[str, Any] | None:
    """Return the report entry's ``thresholdGate`` block, if any (defensive)."""
    if not isinstance(entry, DecisionLogEntry) or not isinstance(entry.data, dict):
        return None
    gate = entry.data.get("thresholdGate")
    return gate if isinstance(gate, dict) else None


def threshold_exit_code(gate: dict[str, Any] | None) -> int:
    """Exit-code policy for the threshold gate: 1 when present and failed."""
    if gate is None:
        return 0
    return 0 if bool(gate.get("passed", True)) else 1


def severity_gate(summary: ReportSummary, fail_on: str = DEFAULT_FAIL_ON) -> int:
    """Exit-code policy: 1 when any finding is at or above ``fail_on``."""
    threshold = fail_on.strip().lower()
    if threshold not in SEVERITY_ORDER:
        threshold = DEFAULT_FAIL_ON
    if threshold == "none":
        return 0
    threshold_rank = SEVERITY_ORDER.index(threshold)
    for finding in summary.findings:
        key = str(finding.get("severity") or "").strip().lower()
        if key in SEVERITY_ORDER and SEVERITY_ORDER.index(key) >= threshold_rank:
            return 1
    return 0


__all__ = [
    "DEFAULT_FAIL_ON",
    "GITHUB_ANNOTATION_LEVELS",
    "SEVERITY_ORDER",
    "ReportSummary",
    "latest_report_entry",
    "render_github",
    "render_text",
    "severity_gate",
    "threshold_exit_code",
    "threshold_gate",
]
