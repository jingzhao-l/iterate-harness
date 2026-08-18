"""Report routes (design §17.3 P7).

Lists generated report artifacts (``report.html``, ``replay.html``, CSV)
under the project's ``.iterate`` directory, and serves the HTML content
inline for the frontend's embedded preview panel.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..schemas import ReportView

log = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

#: Report file types the WebUI lists and previews.
REPORT_FILENAMES = ("report.html", "replay.html", "report.csv")


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _to_modified_iso(stat: os.stat_result) -> str | None:
    """Convert a file's mtime to an ISO-8601 UTC timestamp.

    Returns ``None`` only when the mtime cannot be represented (e.g. an
    out-of-range platform value); the conversion is best-effort and logs a
    warning instead of silently swallowing the failure so the report list
    shows a usable ``modified`` for normal files.
    """
    try:
        return datetime.datetime.fromtimestamp(
            stat.st_mtime, tz=datetime.timezone.utc
        ).isoformat()
    except (OSError, OverflowError, ValueError) as exc:
        log.warning("report mtime conversion failed: %s", exc)
        return None


@router.get("/reports", response_model=list[ReportView])
def list_reports(project_root: str = "") -> list[ReportView]:
    """List generated report artifacts in the project's ``.iterate``."""
    root = _resolve_project(project_root)
    report_dir = root / ".iterate"
    out: list[ReportView] = []
    for name in REPORT_FILENAMES:
        path = report_dir / name
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError as exc:
            # A raced deletion or permission change between is_file and stat
            # should not fail the whole listing — log and keep going.
            log.warning("report stat failed for %s: %s", name, exc)
            continue
        out.append(
            ReportView(
                name=name,
                path=str(path.relative_to(root)),
                size=stat.st_size,
                modified=_to_modified_iso(stat),
            )
        )
    return out


@router.get("/reports/preview", response_model=dict[str, object])
def preview_report(
    project_root: str = "",
    name: str = "report.html",
) -> dict[str, object]:
    """Return the full HTML content of a report file for inline preview.

    Uses path-whitelisting (``resolve_within``) to prevent traversal: the
    report file must sit under the project's ``.iterate`` directory.
    """
    root = _resolve_project(project_root)
    from ..security import resolve_within

    try:
        resolved = resolve_within(root / ".iterate", name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"Report file not found: {name}")

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Read failed: {exc}") from exc

    return {"name": name, "content": content, "size": resolved.stat().st_size}