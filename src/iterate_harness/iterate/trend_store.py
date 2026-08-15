"""Finding fingerprint trend library (cross-run new/fixed/stubborn tracking).

Every finished iterate loop appends exactly one ``report`` entry to the
decision log (:mod:`.decision_log`). When that entry lands, this module
records the run's findings into a per-project library keyed by a
``file|line|dimension`` fingerprint so later runs can answer:

- **new** — fingerprint never seen in any previous run;
- **fixed** — open at the end of the previous run, absent from this one;
- **regressed** — previously fixed, reappeared in this run;
- **stubborn** — still open after ``STUBBORN_MIN_RUNS`` runs (>= 3).

Storage mirrors :mod:`.personalization`: a JSON file under
``.iterate/trend-library.json`` at the project root, atomic write via
temp file + rename, corrupt data resets to empty instead of crashing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TREND_LIBRARY_FILENAME = "trend-library.json"

#: A finding open for this many runs (including the current one) is stubborn.
STUBBORN_MIN_RUNS = 3

#: Library size cap: keep the most recently seen records (by last_seen).
MAX_TRACKED_FINDINGS = 2000

STATUS_OPEN = "open"
STATUS_FIXED = "fixed"


def finding_fingerprint(finding: dict[str, Any]) -> str | None:
    """Compute the stable ``file|line|dimension`` fingerprint for a finding.

    ``line`` participates only when set (> 0): a finding without a line is
    file-level. Returns ``None`` for findings without a usable file+dimension
    (they cannot be tracked meaningfully).
    """
    file_name = str(finding.get("file") or "").strip()
    dimension = str(finding.get("dimension") or "").strip()
    if not file_name or not dimension:
        return None
    line_value = finding.get("line")
    line_part = str(line_value) if isinstance(line_value, int) and line_value > 0 else "0"
    key = f"{file_name}|{line_part}|{dimension}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


@dataclass
class TrendRecord:
    """One tracked finding across runs."""

    fingerprint: str
    file: str
    dimension: str
    line: int | None = None
    severity: str = "medium"
    summary: str = ""
    first_seen: str = ""
    last_seen: str = ""
    runs: int = 0
    status: str = STATUS_OPEN
    fixed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "file": self.file,
            "dimension": self.dimension,
            "line": self.line,
            "severity": self.severity,
            "summary": self.summary,
            "firstSeen": self.first_seen,
            "lastSeen": self.last_seen,
            "runs": self.runs,
            "status": self.status,
            "fixedAt": self.fixed_at,
        }


@dataclass
class TrendDelta:
    """What changed between the previous run and the recorded one."""

    run_timestamp: str = ""
    new_findings: list[dict[str, Any]] = field(default_factory=list)
    fixed_findings: list[dict[str, Any]] = field(default_factory=list)
    regressed_findings: list[dict[str, Any]] = field(default_factory=list)
    stubborn_findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runTimestamp": self.run_timestamp,
            "new": len(self.new_findings),
            "fixed": len(self.fixed_findings),
            "regressed": len(self.regressed_findings),
            "stubborn": len(self.stubborn_findings),
            "newFindings": self.new_findings,
            "fixedFindings": self.fixed_findings,
            "regressedFindings": self.regressed_findings,
            "stubbornFindings": self.stubborn_findings,
        }


def library_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".iterate" / TREND_LIBRARY_FILENAME


def load_library(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """Load the trend library; corrupt or missing data resets to empty."""
    target = library_path(project_root)
    if not target.exists():
        return {}
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("corrupt trend library %s — resetting to empty", target)
        return {}
    if not isinstance(parsed, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, dict):
            records[key] = value
    return records


def _save_library(project_root: str | Path, records: dict[str, dict[str, Any]]) -> Path:
    target = library_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def _record_from_finding(fingerprint: str, finding: dict[str, Any], now: str) -> TrendRecord:
    line_value = finding.get("line")
    return TrendRecord(
        fingerprint=fingerprint,
        file=str(finding.get("file") or ""),
        dimension=str(finding.get("dimension") or ""),
        line=line_value if isinstance(line_value, int) and line_value > 0 else None,
        severity=str(finding.get("severity") or "medium"),
        summary=str(finding.get("summary") or ""),
        first_seen=now,
        last_seen=now,
        runs=1,
        status=STATUS_OPEN,
    )


def _apply_run_updates(
    records: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
    now: str,
) -> TrendDelta:
    delta = TrendDelta(run_timestamp=now)
    current: dict[str, dict[str, Any]] = {}
    for finding in findings:
        fingerprint = finding_fingerprint(finding)
        if fingerprint is None or fingerprint in current:
            continue
        current[fingerprint] = finding

    # Classify against the previous state.
    for fingerprint, finding in current.items():
        existing = records.get(fingerprint)
        if existing is None:
            record = _record_from_finding(fingerprint, finding, now)
            delta.new_findings.append(record.to_dict())
        else:
            record = TrendRecord(
                fingerprint=fingerprint,
                file=str(existing.get("file") or finding.get("file") or ""),
                dimension=str(existing.get("dimension") or finding.get("dimension") or ""),
                line=existing.get("line") if isinstance(existing.get("line"), int) else None,
                severity=str(finding.get("severity") or existing.get("severity") or "medium"),
                summary=str(finding.get("summary") or existing.get("summary") or ""),
                first_seen=str(existing.get("first_seen") or now),
                last_seen=now,
                runs=int(existing.get("runs") or 0) + 1,
                status=STATUS_OPEN,
            )
            if existing.get("status") == STATUS_FIXED:
                delta.regressed_findings.append(record.to_dict())
        records[fingerprint] = record.to_dict()

    # Anything open before and absent now is fixed.
    for fingerprint, existing in list(records.items()):
        if fingerprint in current or existing.get("status") != STATUS_OPEN:
            continue
        existing["status"] = STATUS_FIXED
        existing["fixed_at"] = now
        delta.fixed_findings.append(dict(existing))

    # Stubborn: still open with enough accumulated runs.
    for fingerprint in current:
        record = records[fingerprint]
        if record["status"] == STATUS_OPEN and record["runs"] >= STUBBORN_MIN_RUNS:
            delta.stubborn_findings.append(dict(record))
    return delta


def _prune(records: dict[str, dict[str, Any]]) -> None:
    if len(records) <= MAX_TRACKED_FINDINGS:
        return
    ordered = sorted(records.items(), key=lambda item: str(item[1].get("last_seen") or ""))
    for fingerprint, _ in ordered[: len(records) - MAX_TRACKED_FINDINGS]:
        del records[fingerprint]


def record_run(
    project_root: str | Path,
    findings: list[dict[str, Any]] | None,
    run_timestamp: str | None = None,
) -> TrendDelta:
    """Record one finished run's findings into the trend library.

    Returns the delta vs. the previous run (new / fixed / regressed /
    stubborn). Malformed finding entries are skipped defensively; a failure
    to persist never propagates to the caller's loop.
    """
    now = run_timestamp or datetime.now(UTC).isoformat()
    safe_findings = [f for f in (findings or []) if isinstance(f, dict)]
    records = load_library(project_root)
    delta = _apply_run_updates(records, safe_findings, now)
    _prune(records)
    _save_library(project_root, records)
    return delta


def summarize(project_root: str | Path, top: int = 20) -> dict[str, Any]:
    """Build the cross-run trend summary shown by ``ih iterate log --trend``."""
    records = load_library(project_root)
    open_records = [r for r in records.values() if r.get("status") == STATUS_OPEN]
    fixed_records = [r for r in records.values() if r.get("status") == STATUS_FIXED]
    stubborn = sorted(
        (r for r in open_records if int(r.get("runs") or 0) >= STUBBORN_MIN_RUNS),
        key=lambda r: -int(r.get("runs") or 0),
    )
    return {
        "trackedFindings": len(records),
        "open": len(open_records),
        "fixed": len(fixed_records),
        "stubborn": len(stubborn),
        "stubbornFindings": [dict(r) for r in stubborn[: max(1, top)]],
    }


def render_trend_summary(summary: dict[str, Any]) -> str:
    """Render the trend summary as plain text."""
    lines = [
        (
            "iterate trend library (.iterate/trend-library.json): "
            f"{summary.get('trackedFindings', 0)} tracked finding(s) — "
            f"{summary.get('open', 0)} open, "
            f"{summary.get('fixed', 0)} fixed, "
            f"{summary.get('stubborn', 0)} stubborn"
        )
    ]
    stubborn = summary.get("stubbornFindings") or []
    if not stubborn:
        lines.append("  no stubborn findings (nothing open for 3+ runs)")
        return "\n".join(lines)
    lines.append("  stubborn findings (open for 3+ runs):")
    for record in stubborn:
        location = str(record.get("file") or "?")
        line_value = record.get("line")
        if isinstance(line_value, int) and line_value > 0:
            location += f":{line_value}"
        lines.append(
            f"    [{record.get('severity') or '?'}] {location} "
            f"{record.get('dimension') or '?'} — {int(record.get('runs') or 0)} runs — "
            f"{str(record.get('summary') or '')[:100]}".rstrip()
        )
    return "\n".join(lines)


__all__ = [
    "MAX_TRACKED_FINDINGS",
    "STATUS_FIXED",
    "STATUS_OPEN",
    "STUBBORN_MIN_RUNS",
    "TREND_LIBRARY_FILENAME",
    "TrendDelta",
    "TrendRecord",
    "finding_fingerprint",
    "library_path",
    "load_library",
    "record_run",
    "render_trend_summary",
    "summarize",
]
