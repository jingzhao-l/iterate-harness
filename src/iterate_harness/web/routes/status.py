"""Dashboard aggregate route (design §17.3 P1).

Builds a single status payload consumed by the ``/`` dashboard: the last
run summary (verdict / mode / rounds / findings), the convergence curve
(findings per round), budget totals, effective config highlights, recent
report files, and the most recent audit entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ...iterate.ci_report import latest_report_entry
from ...iterate.config_loader import load_effective_config
from ...iterate.decision_log import DecisionLogEntry, read_entries
from ...iterate.last_state import summarize_last_run
from .._coerce import as_float, as_int, as_list
from ..security import read_audit_entries
from ..schemas import StatusResponse

router = APIRouter(tags=["status"])

#: Report files (relative to the project's ``.iterate`` dir) surfaced on the
#: dashboard's "recent reports" section.
REPORT_FILENAMES = ("report.html", "replay.html", "report.csv")


def _convergence_curve(entries: list[DecisionLogEntry]) -> list[int]:
    """Extract the findings-per-round curve from report data (defensive).

    Prefers the report entry's ``findingsByRound`` array; when absent,
    falls back to counting ``review_result`` findings per round from the
    decision log so an interrupted run still shows a curve.
    """
    report = latest_report_entry(entries)
    if report is not None and isinstance(report.data, dict):
        raw = report.data.get("findingsByRound")
        if isinstance(raw, list):
            curve = [int(x) for x in raw if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if curve:
                return curve
    per_round: dict[int, int] = {}
    for entry in entries:
        if entry.type != "review_result":
            continue
        if not isinstance(entry.data, dict):
            continue
        raw = entry.data.get("findings")
        if not isinstance(raw, list):
            continue
        count = sum(1 for f in raw if isinstance(f, dict))
        per_round[entry.round] = per_round.get(entry.round, 0) + count
    if per_round:
        return [per_round.get(round_no, 0) for round_no in sorted(per_round)]
    return []


def _budget_view(project_root: Path) -> dict[str, Any]:
    """Budget + rate-limit aggregate from checkpoint, report, and config.

    Token/cost figures come from the persisted checkpoint (interrupted) or
    the latest report entry; the configured caps come from the effective
    config so the dashboard can show "used vs cap".
    """
    from ...iterate.checkpoint import load_checkpoint

    effective = load_effective_config(project_root)
    config = effective.config
    entries = read_entries(project_root)
    report = latest_report_entry(entries)
    report_data = report.data if report is not None and isinstance(report.data, dict) else {}
    checkpoint = load_checkpoint(project_root) or {}

    used_tokens = as_int(report_data.get("totalTokens")) or as_int(checkpoint.get("input_tokens", 0))
    used_usd = as_float(report_data.get("totalCostUsd")) or as_float(checkpoint.get("cost_usd", 0.0))
    # input/output tokens from the checkpoint when a report is missing.
    if not report_data.get("totalTokens"):
        used_tokens = as_int(checkpoint.get("input_tokens", 0)) + as_int(
            checkpoint.get("output_tokens", 0)
        )
    return {
        "usedTokens": used_tokens,
        "usedUsd": round(used_usd, 6),
        "tokenBudget": config.token_budget,
        "budgetUsd": config.budget_usd,
        "maxTurnsPerMinute": config.max_turns_per_minute,
        "exhaustedDimensions": as_list(report_data.get("exhaustedDimensions")),
    }


def _config_highlights(project_root: Path) -> dict[str, Any]:
    """Effective-config highlights for the dashboard (no credentials)."""
    effective = load_effective_config(project_root)
    config = effective.config
    return {
        "mode": "override" if effective.override else "defaults",
        "goal": config.goal,
        "maxRounds": config.max_rounds,
        "language": config.language,
        "dimensions": list(config.dimensions),
        "worktreeIsolation": config.worktree_isolation,
        "thresholdsConfigured": not config.thresholds.is_empty(),
    }


def _report_files(project_root: Path) -> list[dict[str, Any]]:
    """List generated report artifacts under ``.iterate/`` (path-whitelisted)."""
    report_dir = project_root / ".iterate"
    out: list[dict[str, Any]] = []
    for name in REPORT_FILENAMES:
        path = report_dir / name
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "name": name,
                "path": str(path.relative_to(project_root)),
                "size": stat.st_size,
            }
        )
    return out


@router.get("/status", response_model=StatusResponse)
def get_status(project_root: str = "") -> StatusResponse:
    """Aggregate dashboard payload for the current project."""
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")

    entries = read_entries(root)
    last_run = summarize_last_run(str(root))
    latest_round = max((entry.round for entry in entries), default=0)
    return StatusResponse(
        project_root=str(root.resolve()),
        last_run=last_run,
        entry_count=len(entries),
        latest_round=latest_round,
        convergence=_convergence_curve(entries),
        budget=_budget_view(root),
        config=_config_highlights(root),
        reports=_report_files(root),
        audit_recent=read_audit_entries(root, limit=10),
    )


@router.get("/health", response_model=dict[str, object])
def get_health(project_root: str = "") -> dict[str, object]:
    """Lightweight liveness probe (no heavy reads)."""
    root = Path(project_root) if project_root else Path.cwd()
    return {
        "status": "ok",
        "project_root": str(root.resolve()) if root.is_dir() else str(root),
    }
