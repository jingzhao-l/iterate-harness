"""Checkpoint routes (design §17.3 P3).

Lists the persisted checkpoint state and performs the controlled "restore"
operation (regenerate the checkpoint so a resume can continue from the last
convergence point). Restore is a mutating operation: it goes through the
audit log and requires an explicit ``confirm=true`` flag (the frontend shows
a secondary confirmation dialog before sending it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ...iterate.checkpoint import load_checkpoint
from ...iterate.ci_report import latest_report_entry
from ...iterate.decision_log import read_entries
from ...iterate.last_state import summarize_last_run
from ..security import AuditLog
from .._coerce import as_int
from ..schemas import CheckpointView, OperationResult

router = APIRouter(tags=["checkpoints"])


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


@router.get("/checkpoints", response_model=CheckpointView)
def get_checkpoint(project_root: str = "") -> CheckpointView:
    """Current checkpoint + latest report context (interrupted flag)."""
    root = _resolve_project(project_root)
    checkpoint = load_checkpoint(root)
    entries = read_entries(root)
    report = latest_report_entry(entries)
    summary = summarize_last_run(str(root))
    interrupted = bool(summary and summary.get("interrupted"))
    last_report: dict[str, Any] | None = None
    if report is not None and isinstance(report.data, dict):
        last_report = {
            "timestamp": report.timestamp,
            "round": report.round,
            "verdict": str(report.data.get("verdict") or "unknown"),
            "mode": str(report.data.get("mode") or "dry-run"),
            "totalFindings": as_int(report.data.get("totalFindings")),
        }
    return CheckpointView(
        exists=checkpoint is not None,
        checkpoint=checkpoint,
        last_report=last_report,
        interrupted=interrupted,
    )


@router.post("/checkpoints/restore", response_model=OperationResult)
def restore_checkpoint(
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Re-arm the checkpoint for a resume (mutating, audited).

    This does not clear the checkpoint; it re-confirms the latest persisted
    convergence point is available for ``/iterate resume`` and records the
    operation in the audit log. Requires ``confirm=true``.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="restore requires confirm=true (secondary confirmation)",
        )
    checkpoint = load_checkpoint(root)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="No checkpoint to restore")
    # Malformed persisted "round" (string, dict, list, non-int float) must
    # degrade to a safe default instead of raising 500.
    round_value = as_int(checkpoint.get("round"))
    AuditLog(root).record(
        "checkpoint.restore",
        "checkpoint.json",
        summary={"round": round_value},
    )
    return OperationResult(
        status="ok",
        message=f"Checkpoint armed for resume (round {round_value})",
        target="checkpoint.json",
        detail={"round": round_value},
    )


@router.post("/checkpoints/clear", response_model=OperationResult)
def clear_checkpoint(
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Clear the checkpoint (mutating, audited).

    Used to discard a stale interrupted-run checkpoint before starting a
    fresh run. Requires ``confirm=true``.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="clear requires confirm=true (secondary confirmation)",
        )
    from ...iterate.checkpoint import clear_checkpoint as _clear

    had_checkpoint = load_checkpoint(root) is not None
    _clear(root)
    AuditLog(root).record("checkpoint.clear", "checkpoint.json", summary={"had": had_checkpoint})
    return OperationResult(
        status="ok",
        message="Checkpoint cleared" if had_checkpoint else "No checkpoint was present",
        target="checkpoint.json",
        detail={"had": had_checkpoint},
    )
