"""Decision-log routes: runs overview, per-run timeline, findings (design §17.3 P2).

Serves the trajectory-style replay: every decision-log entry as a timeline
item, grouped by round, plus a findings table with optional diff expansion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...iterate.ci_report import ReportSummary, latest_report_entry
from ...iterate.decision_log import DecisionLogEntry, read_entries
from ...iterate.html_report import REPLAY_ENTRY_TYPES
from .. import findings_triage
from ..schemas import (
    FindingsTriageDismissRequest,
    FindingsTriageRequest,
    OperationResult,
    RunSummary,
    TimelineEntry,
)
from ..security import AuditLog

router = APIRouter(tags=["runs"])

#: Default pagination page size for the runs overview.
DEFAULT_PAGE_SIZE = 50

#: Upper bound on a single findings page returned by the timeline endpoint.
MAX_FINDINGS_PAGE = 500


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _entry_to_summary(index: int, entry: DecisionLogEntry) -> RunSummary:
    return RunSummary(
        index=index,
        timestamp=entry.timestamp,
        round=entry.round,
        type=entry.type,
        data=dict(entry.data),
    )


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    project_root: str = "",
    offset: int = Query(0, ge=0, description="Entry offset"),
    limit: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, le=500, description="Max entries returned"
    ),
) -> list[RunSummary]:
    """Paged decision-log overview (oldest first)."""
    entries = read_entries(_resolve_project(project_root))
    page = entries[offset : offset + limit]
    return [_entry_to_summary(offset + idx, entry) for idx, entry in enumerate(page)]


@router.get("/runs/timeline", response_model=list[TimelineEntry])
def get_run_timeline(
    project_root: str = "",
    round: int = Query(-1, description="Filter to one round (-1 = all)"),
    type: str = Query("", description="Filter to one entry type (empty = all)"),
    offset: int = Query(
        0, ge=0, description="Page offset from the newest entry (0 = newest page)"
    ),
    limit: int = Query(
        200, ge=1, le=2000, description="Max timeline entries returned"
    ),
) -> list[TimelineEntry]:
    """Trajectory-style timeline of decision-log entries.

    Optional filters: ``round`` (an integer round number) and ``type`` (one
    of the replay entry types). Entries are returned oldest-first, paged
    from the newest: ``offset=0`` returns the newest ``limit`` entries,
    ``offset=40`` returns the 40 previous ones (and so on). The returned
    ``index`` is the entry position in the *filtered* log so the frontend
    can keep diff expansion stable across pages.
    """
    entries = read_entries(_resolve_project(project_root))
    allowed_types = set(REPLAY_ENTRY_TYPES)
    filtered = entries
    if round >= 0:
        filtered = [e for e in filtered if e.round == round]
    if type:
        if type not in allowed_types:
            raise HTTPException(
                status_code=422,
                detail=f"unknown entry type: {type} (allowed: {', '.join(sorted(allowed_types))})",
            )
        filtered = [e for e in filtered if e.type == type]
    if offset <= 0:
        page = filtered[-limit:] if limit else filtered
        base = max(0, len(filtered) - len(page))
    else:
        # Offset counts backwards from the newest entry.
        start = max(0, len(filtered) - offset - limit)
        end = len(filtered) - offset
        page = filtered[start:end]
        base = start
    return [
        TimelineEntry(
            index=base + idx,
            timestamp=entry.timestamp,
            round=entry.round,
            type=entry.type,
            data=dict(entry.data),
        )
        for idx, entry in enumerate(page)
    ]


@router.get("/runs/findings", response_model=dict[str, Any])
def get_findings(
    project_root: str = "",
    round: int = Query(-1, description="Restrict findings to one round (-1 = all)"),
    severity: str = Query("", description="Filter by severity (empty = all)"),
    dimension: str = Query("", description="Filter by dimension (empty = all)"),
    limit: int = Query(
        MAX_FINDINGS_PAGE, ge=1, le=MAX_FINDINGS_PAGE, description="Max findings"
    ),
) -> dict[str, Any]:
    """Findings table: gathered from review_result + report entries.

    Returns ``{"findings": [...], "total": n, "page": m}``. Filters on
    ``severity`` / ``dimension`` are exact-match on the finding fields.
    """
    entries = read_entries(_resolve_project(project_root))
    findings: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for entry in entries:
        if entry.type not in ("review_result", "report"):
            continue
        if round >= 0 and entry.round != round:
            continue
        raw = entry.data.get("findings") if isinstance(entry.data, dict) else None
        if not isinstance(raw, list):
            continue
        for finding in raw:
            if not isinstance(finding, dict):
                continue
            # Apply severity/dimension filters BEFORE deduplication: the same
            # (file, line, dimension) key re-reported in a later round with a
            # different severity must stay visible under a filtered view.
            # (Previously the round-1 occurrence was deduped first, hiding the
            # round-2 re-report that actually matched the filter.)
            if severity and str(finding.get("severity") or "").lower() != severity.lower():
                continue
            if dimension and str(finding.get("dimension") or "") != dimension:
                continue
            # Deduplicate by (file, line, dimension) across entries so the
            # same finding reported in later rounds appears once.
            dedup_key = (
                finding.get("file"),
                finding.get("line"),
                finding.get("dimension"),
            )
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            findings.append(dict(finding))

    total = len(findings)
    page = findings[:limit]
    return {"findings": page, "total": total, "page": len(page)}


@router.get("/runs/report", response_model=dict[str, Any])
def get_latest_report(
    project_root: str = "",
) -> dict[str, Any]:
    """The latest report entry as a plain payload (verdict, mode, summary)."""
    entries = read_entries(_resolve_project(project_root))
    report = latest_report_entry(entries)
    if report is None:
        raise HTTPException(status_code=404, detail="No report entry in the decision log")
    summary = ReportSummary.from_entry(report)
    return {
        "timestamp": report.timestamp,
        "round": report.round,
        "mode": summary.mode,
        "verdict": summary.verdict,
        "totalFindings": summary.total_findings,
        "data": dict(report.data) if isinstance(report.data, dict) else {},
    }


@router.get("/runs/findings/triage", response_model=list[dict[str, Any]])
def list_findings_triage(
    project_root: str = "",
) -> list[dict[str, Any]]:
    """All persisted findings-triage decisions, most recent first (design §17.3 P2).

    These are the human "approve / reject" records written from the Runs page
    findings table. The latest decision for a ``(file, line, dimension)`` key
    wins; re-triaging appends a new line instead of rewriting history.
    """
    return findings_triage.list_decisions(_resolve_project(project_root))


@router.post("/runs/findings/triage", response_model=OperationResult)
def record_findings_triage(
    body: FindingsTriageRequest,
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Record one findings-triage decision (approve / reject), audited.

    Requires ``confirm=true`` (the frontend always sends it). Appends to the
    append-only journal; returns the stored record in ``detail``.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="triage requires confirm=true (secondary confirmation)",
        )
    try:
        record = findings_triage.record_decision(
            root,
            file=body.file,
            line=body.line,
            dimension=body.dimension,
            decision=body.decision,
            note=body.note,
        )
    except OSError as exc:
        # A failed journal write must surface as a real error, never a
        # phantom success (the caller sees a 500 instead of a false "ok").
        raise HTTPException(
            status_code=500,
            detail=f"triage journal write failed: {exc}",
        ) from exc
    AuditLog(root).record(
        "findings.triage",
        body.decision,
        summary={
            "file": body.file,
            "line": body.line,
            "dimension": body.dimension,
        },
    )
    return OperationResult(
        status="ok",
        message=f"已记录审批：{body.decision}",
        target=body.file,
        detail={"record": record},
    )


@router.delete("/runs/findings/triage/dismiss", response_model=OperationResult)
def dismiss_findings_triage(
    body: FindingsTriageDismissRequest,
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Dismiss (remove) one persisted triage decision, audited.

    Requires ``confirm=true`` (the frontend always sends it). Unlike the
    ``DELETE /runs/findings/triage`` full clear, this targets a single
    (file, line, dimension) key via :func:`findings_triage.clear_decision`.
    Returns how many decisions were removed (``1`` on success, ``0`` when
    the finding had no persisted decision).
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="dismiss triage requires confirm=true (secondary confirmation)",
        )
    try:
        removed = findings_triage.clear_decision(
            root,
            file=body.file,
            line=body.line,
            dimension=body.dimension,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"triage journal write failed: {exc}",
        ) from exc
    AuditLog(root).record(
        "findings.triage.dismiss",
        body.file,
        summary={
            "file": body.file,
            "line": body.line,
            "dimension": body.dimension,
            "removed": removed,
        },
    )
    return OperationResult(
        status="ok",
        message="已撤销审批记录" if removed else "未找到对应的审批记录",
        target=body.file,
        detail={
            "file": body.file,
            "line": body.line,
            "dimension": body.dimension,
            "removed": removed,
        },
    )


@router.delete("/runs/findings/triage", response_model=OperationResult)
def clear_findings_triage(
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Clear every persisted triage decision (compaction), audited.

    Requires ``confirm=true``. Returns how many decisions were removed.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="clear triage requires confirm=true (secondary confirmation)",
        )
    try:
        removed = findings_triage.clear_all(root)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"triage journal write failed: {exc}",
        ) from exc
    AuditLog(root).record("findings.triage.clear", "all", summary={"removed": removed})
    return OperationResult(
        status="ok",
        message=f"已清除 {removed} 条审批记录",
        detail={"removed": removed},
    )
