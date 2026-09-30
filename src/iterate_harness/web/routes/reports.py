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

from ..security import allowed_roots, root_is_allowed
from ..schemas import ReportView

log = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

#: Report file types the WebUI lists and previews.
REPORT_FILENAMES = ("report.html", "replay.html", "report.csv")

#: Largest report the inline preview will buffer into a JSON response.
MAX_PREVIEW_BYTES = 8 * 1024 * 1024


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    # ``project_root`` is caller-controlled, so containment inside the root is
    # not enough on its own: without this the parameter *selects* the root, and
    # a valid token granted read/write over any directory on the machine.
    if not root_is_allowed(root):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Project root is outside the roots this WebUI serves: {root}. "
                f"Allowed: {', '.join(sorted(allowed_roots())) or '(none)'}"
            ),
        )
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

    # The path is inside .iterate but must still be one of the known report
    # artifacts; otherwise a caller could read any file in .iterate (e.g. the
    # audit/triage journals) by passing its name here.
    if resolved.name not in REPORT_FILENAMES:
        raise HTTPException(status_code=404, detail=f"Report file not found: {name}")

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"Report file not found: {name}")

    # A long run's report.html is routinely tens of MB. Buffering the whole
    # file into one JSON string doubled memory and hung the tab on JSON.parse,
    # so refuse politely with an actionable message instead of stalling.
    size = resolved.stat().st_size
    if size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"报告过大（{size} 字节 > {MAX_PREVIEW_BYTES} 字节），无法在线预览。"
                f"请直接打开文件：{resolved}"
            ),
        )

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("report preview read failed for %s: %s", resolved, exc)
        raise HTTPException(
            status_code=500, detail="Read failed (see the server log)"
        ) from exc

    return {"name": name, "content": content, "size": size}