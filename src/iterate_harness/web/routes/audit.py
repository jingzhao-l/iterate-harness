"""Audit-trail routes (design §17.3 P13).

The console could *write* audit records (config saves, checkpoint restores,
schedule changes, triage decisions, agent shutdowns) but only ever *show* the
newest ten lines on the dashboard. This route is the read side that closes
that loop: a paged, filterable trail with three export shapes, backed by the
same shared reader the ``ih audit`` CLI uses. Read-only: it never writes the
journal or any other project state.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from ...audit import (
    audit_csv,
    audit_export_filename,
    distinct_actions,
    list_audit_entries,
    render_audit_markdown,
)
from ..security import allowed_roots, default_root, root_is_allowed
from ..schemas import AuditEntry, AuditView

router = APIRouter(tags=["audit"])

#: Upper bound on a single audit page (and export scan).
MAX_AUDIT_PAGE = 500


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else (default_root() or Path.cwd())
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


def _parse_since(since: str) -> datetime.datetime | None:
    """Parse the ``since`` filter (ISO 8601, naive treated as UTC).

    An unparsable value is a 422, not a silent "filter ignored": an operator
    typing a bogus date into the page should be told, not given a full trail
    that looks filtered.
    """
    if not since:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(since)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid since value: {since!r} (expected ISO 8601, e.g. 2026-11-01T00:00:00)",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _page(project_root: Path, *, action: str, target: str, since: str, limit: int, offset: int) -> AuditView:
    """Build the paged view for a project (shared by page + export readers)."""
    since_dt = _parse_since(since)
    entries, total = list_audit_entries(
        project_root,
        action=action,
        target=target,
        since=since_dt,
        limit=limit,
        offset=offset,
    )
    return AuditView(
        project_root=str(project_root),
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        total=total,
        returned=len(entries),
        offset=offset,
        limit=limit,
        truncated=offset + len(entries) < total,
        actions=distinct_actions(project_root),
        entries=[AuditEntry(**entry) for entry in entries],
    )


@router.get("/audit", response_model=AuditView)
def get_audit(
    project_root: str = "",
    action: str = Query("", description="Filter by action prefix (e.g. 'config' matches 'config.save')"),
    target: str = Query("", description="Filter by target substring"),
    since: str = Query("", description="Only entries at/after this ISO 8601 timestamp"),
    offset: int = Query(0, ge=0, description="Page offset from the newest entry"),
    limit: int = Query(50, ge=1, le=MAX_AUDIT_PAGE, description="Max entries returned"),
) -> AuditView:
    """Paged audit trail (newest first), with filters and the action list.

    Read-only. The journal is append-only and never rotated, so this scans the
    file on demand — acceptable for the local control surface, and bounded by
    ``MAX_AUDIT_PAGE`` per response.
    """
    root = _resolve_project(project_root)
    return _page(root, action=action, target=target, since=since, limit=limit, offset=offset)


@router.get("/audit/export")
def export_audit(
    project_root: str = "",
    action: str = Query("", description="Filter by action prefix"),
    target: str = Query("", description="Filter by target substring"),
    since: str = Query("", description="Only entries at/after this ISO 8601 timestamp"),
    fmt: Literal["md", "csv", "json"] = Query("md", alias="format"),
) -> Response:
    """Download the filtered audit trail as Markdown / CSV / JSON.

    Markdown is the paste-into-a-PR-or-incident-report digest; CSV is one row
    per entry for a spreadsheet; JSON is the exact ``AuditView`` payload.
    Read-only, like the page.
    """
    root = _resolve_project(project_root)
    since_dt = _parse_since(since)
    # Export uses the same filters as the page but is not paginated — a
    # compliance export must carry the whole filtered window, not page 1.
    entries, total = list_audit_entries(
        root,
        action=action,
        target=target,
        since=since_dt,
        limit=MAX_AUDIT_PAGE * 20,
        offset=0,
    )
    if fmt == "json":
        view = _page(root, action=action, target=target, since=since, limit=MAX_AUDIT_PAGE, offset=0)
        body = json.dumps(view.model_dump(), ensure_ascii=False, indent=2) + "\n"
        media_type = "application/json"
    elif fmt == "csv":
        body = audit_csv(entries)
        media_type = "text/csv; charset=utf-8"
    else:
        body = render_audit_markdown(entries, root)
        media_type = "text/markdown; charset=utf-8"
    filename = audit_export_filename(fmt)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audit-Total": str(total),
        },
    )


__all__ = ["router"]
