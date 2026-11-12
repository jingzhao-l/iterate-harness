"""Lifecycle-hook routes (design §17.3 P15).

The engine loads lifecycle hooks from settings and enabled plugins and runs
them inline in front of tool calls and turn boundaries, but a browser operator
could not see any of it: a hook registered under a misspelled event name was
silently dropped, and a hook that rejected a tool call only appeared as a
mystery refusal inside a run. These two routes close that gap with a read-only
inventory of registered hooks (``GET /api/v1/hooks``) and a paged,
filterable trail of every hook execution (``GET /api/v1/hooks/log``) — one
shared loader and one shared journal reader with the CLI, so the console and
``ih hooks`` can never disagree. Both routes are read-only and never start a
model or a hook command.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..roots import resolve_project_root
from ..schemas import HookLogEntry, HookLogView, HooksView
from ...hooks.inventory import build_hook_inventory
from ...hooks.journal import (
    distinct_events,
    distinct_hook_types,
    hook_export_filename,
    hook_log_csv,
    list_hook_executions,
    render_hook_log_markdown,
)

UTC = timezone.utc

router = APIRouter(tags=["hooks"])


def _resolve_project(request: Request, project_root: str) -> Path:
    """Resolve and allow-list the project root (mirrors the other routes)."""
    root = resolve_project_root(request, project_root)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _parse_success(success: str) -> bool | None:
    """Parse the ``success`` filter; a bogus value is a 422, not "all".

    The CLI already rejects a bad ``--success`` value explicitly; a page that
    silently shows every row for a filter the operator typed is a worse answer
    than a clear error.
    """
    value = success.strip().lower()
    if value in {"", "all"}:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise HTTPException(
        status_code=422,
        detail=f"invalid success value: {success!r} (expected true, false, or empty)",
    )


@router.get("/hooks", response_model=HooksView)
def get_hooks(request: Request, project_root: str = "") -> HooksView:
    """List every registered lifecycle hook and the execution-trail summary.

    Read-only and network-free: no hook command is spawned and no model is
    invoked. Unknown event names are reported inside ``problems`` (with a
    copyable remedy) rather than hidden — a hook the loader silently drops is
    exactly what this page exists to surface.
    """
    root = _resolve_project(request, project_root)
    return HooksView(**build_hook_inventory(root))


@router.get("/hooks/log", response_model=HookLogView)
def get_hook_log(
    request: Request,
    project_root: str = "",
    event: str = Query("", description="Filter by lifecycle event"),
    hook_type: str = Query("", description="Filter by hook type (command / prompt / http / agent)"),
    success: str = Query("", description="Filter by outcome: true / false / '' (all)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> HookLogView:
    """Page the hook-execution trail, newest first.

    ``success`` accepts ``true``/``false`` (case-insensitive); empty means all,
    anything else is a 422. A missing journal is an empty page with ``total`` 0
    — never an error.
    """
    root = _resolve_project(request, project_root)
    success_filter = _parse_success(success)
    page, total = list_hook_executions(
        root,
        event=event,
        hook_type=hook_type,
        success=success_filter,
        limit=limit,
        offset=offset,
    )
    return HookLogView(
        project_root=str(root),
        generated_at=datetime.now(UTC).isoformat(),
        total=total,
        returned=len(page),
        offset=offset,
        limit=limit,
        truncated=offset + len(page) < total,
        events=distinct_events(root),
        types=distinct_hook_types(root),
        entries=[HookLogEntry(**entry) for entry in page],
    )


@router.get("/hooks/log/export")
def export_hook_log(
    request: Request,
    project_root: str = "",
    event: str = Query("", description="Filter by lifecycle event"),
    hook_type: str = Query("", description="Filter by hook type"),
    success: str = Query("", description="Filter by outcome: true / false / '' (all)"),
    fmt: Literal["md", "csv", "json"] = Query("md", alias="format"),
) -> Response:
    """Download the filtered hook-execution trail as Markdown / CSV / JSON.

    The export is not paginated: a diagnostic export must carry the whole
    filtered window, not page 1. Read-only, same reader and filters as the page.
    """
    root = _resolve_project(request, project_root)
    success_filter = _parse_success(success)
    entries, total = list_hook_executions(
        root,
        event=event,
        hook_type=hook_type,
        success=success_filter,
        limit=100_000,
        offset=0,
    )
    if fmt == "json":
        body = json.dumps(
            {
                "project_root": str(root),
                "generated_at": datetime.now(UTC).isoformat(),
                "total": total,
                "returned": len(entries),
                "offset": 0,
                "limit": len(entries),
                "truncated": False,
                "events": distinct_events(root),
                "types": distinct_hook_types(root),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        media_type = "application/json"
    elif fmt == "csv":
        body = hook_log_csv(entries, total, len(entries), 0)
        media_type = "text/csv; charset=utf-8"
    else:
        body = render_hook_log_markdown(entries, total, len(entries), 0)
        media_type = "text/markdown; charset=utf-8"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{hook_export_filename(fmt)}"',
            "X-Hooks-Total": str(total),
        },
    )


__all__ = ["router"]
