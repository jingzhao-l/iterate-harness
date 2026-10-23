"""Cross-run trend route (design §17.3 P8).

Surfaces the per-run new/fixed/regressed history that the CLI ``trend``
command derives from the decision log, joined with the trend library's
open/stubborn findings. Read-only: the page never mutates ``.iterate`` state.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ...iterate.trend_report import build_trend_report
from ..schemas import TrendView
from ..security import allowed_roots, default_root, root_is_allowed

router = APIRouter(tags=["trend"])


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else (default_root() or Path.cwd())
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    if not root_is_allowed(root):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Project root is outside the roots this WebUI serves: {root}. "
                f"Allowed: {', '.join(sorted(allowed_roots())) or '(none)'}"
            ),
        )
    return root


@router.get("/trend", response_model=TrendView)
def get_trend(
    project_root: str = "",
    top: int = Query(20, ge=1, le=500),
    history: int = Query(20, ge=1, le=500),
) -> TrendView:
    """Per-run trend history plus the library's open/stubborn findings."""
    root = _resolve_project(project_root)
    report = build_trend_report(root, history=history, top=top)
    return TrendView(**report.to_dict())


__all__ = ["router"]
