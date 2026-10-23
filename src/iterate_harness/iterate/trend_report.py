"""Cross-run trend report: what each finished run added, fixed, or regressed.

The trend *library* (:mod:`.trend_store`) answers "where does this finding
stand right now". It deliberately keeps one record per fingerprint, so it
cannot answer the question an operator actually asks at the console: *is this
codebase getting better, run over run, and what did the last run change?*
The decision log already carries that history — every finished loop appends
exactly one ``report`` entry with its findings — so this module derives the
per-run delta from it and joins the result with the library's stubborn/open
records.

Nothing here writes: it is a read-only view over ``.iterate`` state, which is
why the CLI command and the WebUI route can share it without either owning the
other's data.

Definitions used throughout (and shown in the CLI header so nobody has to
guess):

- **new** — fingerprint present in this run and absent from the previous one;
- **fixed** — present in the previous run and absent from this one;
- **regressed** — present in this run after being absent from an earlier run
  (a previously fixed finding came back);
- **unchanged** — present in both this run and the previous one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import trend_store
from .ci_report import SEVERITY_ORDER
from .decision_log import findings_from_report, read_entries

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

#: How many finding rows one run's detail payload may carry. Counts are always
#: exact; the lists are for humans and are intentionally bounded so a
#: thousand-finding run cannot turn one API response into megabytes.
MAX_FINDINGS_PER_RUN = 50

#: Default number of runs / rows shown by the CLI and the WebUI route.
DEFAULT_HISTORY = 20
DEFAULT_TOP = 20

_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}


def _severity_rank(value: object) -> int:
    """Rank a finding severity (unknown values sort below ``low``)."""
    return _SEVERITY_RANK.get(str(value or "").strip().lower(), 0)


def _finding_view(finding: dict[str, Any]) -> dict[str, Any]:
    """Keep the finding keys the trend views display, no more.

    The report entry may carry a diff or a long failure scenario; echoing all
    of it into a table is not useful and makes the payload unbounded. The
    fingerprint is included so a UI can dedupe/stabilise rows.
    """
    raw_line = finding.get("line")
    view: dict[str, Any] = {
        "file": str(finding.get("file") or ""),
        # ``bool`` is an ``int`` subclass in Python: ``line: true`` is not a
        # line number (the fingerprint treats it as file-level too), so keep
        # the two views from disagreeing about where the finding lives.
        "line": raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None,
        "dimension": str(finding.get("dimension") or ""),
        "severity": str(finding.get("severity") or "medium"),
        "summary": str(finding.get("summary") or ""),
    }
    fingerprint = trend_store.finding_fingerprint(finding)
    if fingerprint is not None:
        view["fingerprint"] = fingerprint
    return view


def _unique_findings(findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map fingerprint → finding, dropping untrackable/duplicate entries.

    ``findings_from_report`` already de-duplicates by identity in some shapes,
    but two dicts describing the same ``file|line|dimension`` are the same
    finding to the trend library; history must agree with it or the counts
    would disagree with the stubborn list.
    """
    out: dict[str, dict[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        fingerprint = trend_store.finding_fingerprint(finding)
        if fingerprint is None or fingerprint in out:
            continue
        out[fingerprint] = _finding_view(finding)
    return out


def _severity_counts(findings: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings.values():
        severity = str(finding.get("severity") or "medium").lower()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _dimension_counts(findings: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings.values():
        dimension = str(finding.get("dimension") or "unknown")
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts


@dataclass
class TrendRun:
    """One finished run's delta versus the run before it."""

    index: int
    timestamp: str
    round: int
    verdict: str
    mode: str
    total: int
    reported_total: int
    new: int
    fixed: int
    regressed: int
    unchanged: int
    by_severity: dict[str, int] = field(default_factory=dict)
    by_dimension: dict[str, int] = field(default_factory=dict)
    new_findings: list[dict[str, Any]] = field(default_factory=list)
    fixed_findings: list[dict[str, Any]] = field(default_factory=list)
    regressed_findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, include_findings: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "timestamp": self.timestamp,
            "round": self.round,
            "verdict": self.verdict,
            "mode": self.mode,
            "total": self.total,
            "reported_total": self.reported_total,
            "new": self.new,
            "fixed": self.fixed,
            "regressed": self.regressed,
            "unchanged": self.unchanged,
            "by_severity": dict(self.by_severity),
            "by_dimension": dict(self.by_dimension),
        }
        if include_findings:
            payload["new_findings"] = self.new_findings[:MAX_FINDINGS_PER_RUN]
            payload["fixed_findings"] = self.fixed_findings[:MAX_FINDINGS_PER_RUN]
            payload["regressed_findings"] = self.regressed_findings[:MAX_FINDINGS_PER_RUN]
        return payload


@dataclass
class TrendReport:
    """Everything the ``trend`` command and the WebUI page render."""

    project_root: str
    generated_at: str
    total_runs: int
    tracked: int
    open: int
    fixed: int
    stubborn: int
    runs: list[TrendRun] = field(default_factory=list)
    open_findings: list[dict[str, Any]] = field(default_factory=list)
    stubborn_findings: list[dict[str, Any]] = field(default_factory=list)
    open_by_dimension: dict[str, int] = field(default_factory=dict)

    @property
    def latest(self) -> TrendRun | None:
        return self.runs[-1] if self.runs else None

    @property
    def has_data(self) -> bool:
        return bool(self.runs) or self.tracked > 0

    def to_dict(self, *, include_latest_findings: bool = True) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "generated_at": self.generated_at,
            "has_data": self.has_data,
            "total_runs": self.total_runs,
            "tracked": self.tracked,
            "open": self.open,
            "fixed": self.fixed,
            "stubborn": self.stubborn,
            "runs": [run.to_dict() for run in self.runs],
            "latest": (
                self.latest.to_dict(include_findings=include_latest_findings)
                if self.latest is not None
                else None
            ),
            "open_findings": self.open_findings,
            "stubborn_findings": self.stubborn_findings,
            "open_by_dimension": dict(self.open_by_dimension),
        }


def collect_runs(project_root: str | Path) -> list[TrendRun]:
    """Derive every finished run's delta from the decision log.

    A run is a ``report`` entry. Entries that predate the current log format
    are read through :func:`findings_from_report`, so historical reports with
    ``topFindings`` / nested ``summary`` shapes still participate. Malformed
    entries are skipped, never raised: a trend view must not be the reason a
    project's log cannot be read.
    """
    runs: list[TrendRun] = []
    previous: dict[str, dict[str, Any]] = {}
    ever_fixed: set[str] = set()
    for entry in read_entries(project_root):
        if entry.type != "report" or not isinstance(entry.data, dict):
            continue
        data = entry.data
        current = _unique_findings(findings_from_report(data))
        current_fps = set(current)
        previous_fps = set(previous)

        regressed_fps = current_fps & ever_fixed
        new_fps = (current_fps - previous_fps) - regressed_fps
        fixed_fps = previous_fps - current_fps
        unchanged_fps = (current_fps & previous_fps) - regressed_fps

        def pick(fingerprints: set[str], source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                dict(source[fingerprint])
                for fingerprint in sorted(fingerprints)
                if fingerprint in source
            ]

        # ``True`` is an ``int`` in Python: a boolean ``totalFindings`` is a
        # caller bug, not a finding count, and would render as the word "True"
        # in a table column. Anything that is not a real integer falls back to
        # the count of findings actually recovered from the entry.
        reported_raw = data.get("totalFindings")
        reported_total = (
            reported_raw
            if isinstance(reported_raw, int) and not isinstance(reported_raw, bool)
            else len(current)
        )
        runs.append(
            TrendRun(
                index=len(runs) + 1,
                timestamp=entry.timestamp,
                round=entry.round,
                verdict=str(data.get("verdict") or "unknown"),
                mode=str(data.get("mode") or "dry-run"),
                total=len(current),
                reported_total=reported_total,
                new=len(new_fps),
                fixed=len(fixed_fps),
                regressed=len(regressed_fps),
                unchanged=len(unchanged_fps),
                by_severity=_severity_counts(current),
                by_dimension=_dimension_counts(current),
                # ``previous`` holds findings from the run before this one;
                # fixed findings therefore come from there, and regressed ones
                # from the current run (they are present now).
                new_findings=pick(new_fps, current),
                fixed_findings=pick(fixed_fps, previous),
                regressed_findings=pick(regressed_fps, current),
            )
        )
        # A finding that vanished is "fixed" from this run onward. Keep the set
        # for the whole history (never reset): a reappearance after *any*
        # absence is a regression, not a first sighting.
        ever_fixed |= fixed_fps
        previous = current
    return runs


def _record_view(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one trend-library record for display."""
    line = record.get("line")
    return {
        "file": str(record.get("file") or ""),
        "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
        "dimension": str(record.get("dimension") or ""),
        "severity": str(record.get("severity") or "medium"),
        "summary": str(record.get("summary") or ""),
        "runs": int(record.get("runs") or 0),
        "first_seen": str(record.get("first_seen") or ""),
        "last_seen": str(record.get("last_seen") or ""),
    }


def _sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Worst first: severity, then how many runs it has survived."""
    return sorted(
        records,
        key=lambda record: (
            -_severity_rank(record.get("severity")),
            -int(record.get("runs") or 0),
            str(record.get("file") or ""),
            int(record["line"]) if isinstance(record.get("line"), int) else 0,
        ),
    )


def build_trend_report(
    project_root: str | Path,
    *,
    history: int = DEFAULT_HISTORY,
    top: int = DEFAULT_TOP,
) -> TrendReport:
    """Join run history with the trend library into one read-only report."""
    root = Path(project_root)
    all_runs = collect_runs(root)
    limit = max(1, int(history))
    visible_runs = all_runs[-limit:]

    summary = trend_store.summarize(root, top=max(1, int(top)))
    library = trend_store.load_library(root)
    open_records = [
        _record_view(record)
        for record in library.values()
        if record.get("status") == trend_store.STATUS_OPEN
    ]
    open_by_dimension: dict[str, int] = {}
    for record in open_records:
        dimension = str(record.get("dimension") or "unknown")
        open_by_dimension[dimension] = open_by_dimension.get(dimension, 0) + 1

    stubborn_records = [
        _record_view(record)
        for record in (summary.get("stubbornFindings") or [])
        if isinstance(record, dict)
    ]
    return TrendReport(
        project_root=str(root),
        generated_at=datetime.now(UTC).isoformat(),
        total_runs=len(all_runs),
        tracked=int(summary.get("trackedFindings") or 0),
        open=int(summary.get("open") or 0),
        fixed=int(summary.get("fixed") or 0),
        stubborn=int(summary.get("stubborn") or 0),
        runs=visible_runs,
        open_findings=_sort_records(open_records)[: max(1, int(top))],
        stubborn_findings=_sort_records(stubborn_records)[: max(1, int(top))],
        open_by_dimension=open_by_dimension,
    )


def _format_location(finding: dict[str, Any]) -> str:
    location = str(finding.get("file") or "?")
    line = finding.get("line")
    if isinstance(line, int) and line > 0:
        location += f":{line}"
    return location


def _render_finding_line(finding: dict[str, Any], *, prefix: str = "  ") -> str:
    return (
        f"{prefix}[{finding.get('severity') or '?'}] {_format_location(finding)} "
        f"{finding.get('dimension') or '?'} — {str(finding.get('summary') or '')[:100]}".rstrip()
    )


def render_trend_report(report: TrendReport) -> str:
    """Render a trend report as plain text with aligned history columns."""
    if not report.has_data:
        return (
            "No trend data yet: no finished iterate run was recorded "
            "(.iterate/decision-log.jsonl has no report entry).\n"
            "Run `ih iterate run` (or `ih iterate review`) once, then re-run "
            "`ih iterate trend`."
        )

    lines = [
        f"iterate trend — {report.total_runs} finished run(s), showing last {len(report.runs)}",
        (
            f"  tracked {report.tracked} · open {report.open} · fixed {report.fixed} · "
            f"stubborn {report.stubborn} (open {trend_store.STUBBORN_MIN_RUNS}+ runs)"
        ),
    ]

    if report.runs:
        lines.append("")
        header = f"  {'run':>3}  {'timestamp':<25}  {'round':>5}  {'total':>5}  {'new':>4}  {'fixed':>5}  {'regressed':>9}  {'same':>4}  verdict"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for run in report.runs:
            lines.append(
                f"  {run.index:>3}  {run.timestamp[:25]:<25}  {run.round:>5}  {run.total:>5}  "
                f"{run.new:>4}  {run.fixed:>5}  {run.regressed:>9}  {run.unchanged:>4}  {run.verdict}"
            )

        latest = report.latest
        if latest is not None:
            if latest.regressed_findings:
                lines.append("")
                lines.append(f"  latest run regressions ({len(latest.regressed_findings)}):")
                lines.extend(_render_finding_line(f, prefix="    ") for f in latest.regressed_findings)
            if latest.new_findings:
                lines.append("")
                lines.append(f"  latest run new findings ({len(latest.new_findings)}):")
                lines.extend(_render_finding_line(f, prefix="    ") for f in latest.new_findings)
            if latest.fixed_findings:
                lines.append("")
                lines.append(f"  latest run fixed findings ({len(latest.fixed_findings)}):")
                lines.extend(_render_finding_line(f, prefix="    ") for f in latest.fixed_findings)

    if report.stubborn_findings:
        lines.append("")
        lines.append(f"stubborn findings (open for {trend_store.STUBBORN_MIN_RUNS}+ runs):")
        for finding in report.stubborn_findings:
            lines.append(_render_finding_line(finding))
            lines.append(f"    seen {finding.get('runs')} runs, last {finding.get('last_seen') or '?'}")
    elif report.open:
        lines.append("")
        lines.append(
            f"No stubborn findings yet; {report.open} open finding(s) have not reached "
            f"{trend_store.STUBBORN_MIN_RUNS} runs."
        )

    if report.open_by_dimension:
        lines.append("")
        lines.append("open findings by dimension:")
        for dimension, count in sorted(
            report.open_by_dimension.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"  {dimension}: {count}")
    return "\n".join(lines)


def regression_count(report: TrendReport) -> int:
    """Regressions introduced by the latest finished run (0 when no runs)."""
    latest = report.latest
    return 0 if latest is None else latest.regressed


def new_findings_at_or_above(report: TrendReport, severity: str) -> int:
    """Count the latest run's new findings at or above ``severity``.

    ``severity`` is one of ``none|low|medium|high|critical`` (case-insensitive);
    an unknown value gates nothing (0), matching ``iterate report --fail-on``'s
    parser, which also refuses unknown levels at the CLI edge.
    """
    latest = report.latest
    if latest is None:
        return 0
    threshold = _SEVERITY_RANK.get(severity.strip().lower())
    if threshold is None:
        return 0
    return sum(
        1
        for finding in latest.new_findings
        if _severity_rank(finding.get("severity")) >= threshold
    )


__all__ = [
    "DEFAULT_HISTORY",
    "DEFAULT_TOP",
    "MAX_FINDINGS_PER_RUN",
    "SEVERITY_ORDER",
    "TrendReport",
    "TrendRun",
    "build_trend_report",
    "collect_runs",
    "new_findings_at_or_above",
    "regression_count",
    "render_trend_report",
]
