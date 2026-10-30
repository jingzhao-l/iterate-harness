"""Cross-run trend route (design §17.3 P8).

Surfaces the per-run new/fixed/regressed history that the CLI ``trend``
command derives from the decision log, joined with the trend library's
open/stubborn findings. Read-only: the page never mutates ``.iterate`` state.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from ...iterate.trend_report import (
    TrendReport,
    build_trend_report,
    render_trend_markdown,
    trend_csv,
)
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


def _export_filename(fmt: str, generated_at: str) -> str:
    """Name the download after the report's own timestamp (UTC, sortable)."""
    try:
        stamp = datetime.datetime.fromisoformat(generated_at).astimezone(
            datetime.timezone.utc
        ).strftime("%Y%m%d-%H%M%S")
    except ValueError:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"iterate-trend-{stamp}.{fmt}"


def _export_payload(report: TrendReport, fmt: str) -> tuple[str, str]:
    """Return ``(body, media_type)`` for one export format."""
    if fmt == "json":
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", "application/json"
    if fmt == "csv":
        return trend_csv(report), "text/csv; charset=utf-8"
    return render_trend_markdown(report), "text/markdown; charset=utf-8"


@router.get("/trend/export")
def export_trend(
    project_root: str = "",
    top: int = Query(20, ge=1, le=500),
    history: int = Query(20, ge=1, le=500),
    fmt: Literal["md", "csv", "json"] = Query("md", alias="format"),
) -> Response:
    """Download the trend as Markdown / CSV / JSON (same data as the page).

    Markdown is the paste-into-a-PR-or-weekly-report digest; CSV is one row
    per finished run for a spreadsheet; JSON is the exact ``TrendView``
    payload. Read-only, like the page.
    """
    root = _resolve_project(project_root)
    report = build_trend_report(root, history=history, top=top)
    body, media_type = _export_payload(report, fmt)
    filename = _export_filename(fmt, report.generated_at)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
