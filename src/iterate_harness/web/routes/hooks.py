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

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from ..roots import resolve_project_root
from ..schemas import HookLogEntry, HookLogView, HooksView
from ...hooks.inventory import build_hook_inventory
from ...hooks.journal import (
    distinct_events,
    distinct_hook_types,
    list_hook_executions,
)

UTC = timezone.utc

router = APIRouter(tags=["hooks"])


def _resolve_project(request: Request, project_root: str) -> Path:
    """Resolve and allow-list the project root (mirrors the other routes)."""
    root = resolve_project_root(request, project_root)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


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

    ``success`` accepts ``true``/``false`` (case-insensitive); any other value
    means "all". A missing journal is an empty page with ``total`` 0 — never an
    error.
    """
    root = _resolve_project(request, project_root)
    success_filter: bool | None = None
    if success.strip().lower() == "true":
        success_filter = True
    elif success.strip().lower() == "false":
        success_filter = False
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


__all__ = ["router"]
