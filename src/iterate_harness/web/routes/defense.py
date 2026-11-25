"""Defensive-kernel routes (design §17.3 P17).

The kernel snapshots every mutating edit, re-runs the project invariants and
rolls the edit back when a check fails, but the operator could only see that
machinery as a tool error inside a live run or a log line. These two read-only
routes expose the effective guard (resolved with the kernel's own rule set),
the assumptions reconstructed from the decision log, and the append-only
journal of every verdict and rollback -- one shared loader and one shared
journal reader with ``ih defense``, so the console and the CLI can never
disagree. Neither route executes an invariant command or contacts a model; the
page offers the copyable ``ih defense check`` command instead.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ...defensive.inventory import build_defense_inventory
from ...defensive.journal import (
    KINDS,
    OUTCOMES,
    defense_log_csv,
    distinct_kinds,
    distinct_outcomes,
    distinct_tools,
    journal_export_filename,
    list_defense_events,
    render_defense_log_markdown,
)
from ..roots import resolve_project_root
from ..schemas import DefenseEvent, DefenseJournalView, DefenseView

UTC = timezone.utc

router = APIRouter(tags=["defense"])


def _resolve_project(request: Request, project_root: str) -> Path:
    """Resolve and allow-list the project root (mirrors the other routes)."""
    root = resolve_project_root(request, project_root)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _validate_filters(kind: str, outcome: str) -> tuple[str, str]:
    """Reject an unknown filter value instead of silently ignoring it."""
    kind_filter = kind.strip()
    outcome_filter = outcome.strip()
    if kind_filter and kind_filter not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid kind {kind!r} (expected one of {', '.join(sorted(KINDS))})",
        )
    if outcome_filter and outcome_filter not in OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"invalid outcome {outcome!r} "
                f"(expected one of {', '.join(sorted(OUTCOMES))})"
            ),
        )
    return kind_filter, outcome_filter


@router.get("/defense", response_model=DefenseView)
def get_defense(request: Request, project_root: str = "") -> DefenseView:
    """Return the effective guard, assumption outcomes and the journal summary.

    Read-only and command-free: the route reports what the kernel *will* check
    and what it has already rolled back. A configured entry that can never run
    is a ``problems`` row with a copyable remedy rather than a silent rollback
    loop the operator has to discover mid-run.
    """
    root = _resolve_project(request, project_root)
    return DefenseView(**build_defense_inventory(root))


@router.get("/defense/journal", response_model=DefenseJournalView)
def get_defense_journal(
    request: Request,
    project_root: str = "",
    kind: str = Query("", description="Filter by kind: rollback / invariant / manual-check"),
    outcome: str = Query(
        "", description="Filter by outcome: pass / fail / error / unmeasured / failed_tool"
    ),
    tool: str = Query("", description="Filter by the tool that triggered the guard"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> DefenseJournalView:
    """Page the defensive-kernel trail, newest first.

    A missing journal is an empty page with ``total`` 0 -- never an error.
    Unknown ``kind`` / ``outcome`` values are a 422 (a page that silently shows
    everything for a filter it cannot honour is worse than a clear error).
    """
    root = _resolve_project(request, project_root)
    kind_filter, outcome_filter = _validate_filters(kind, outcome)
    tool_filter = tool.strip()
    page, total = list_defense_events(
        root,
        kind=kind_filter,
        outcome=outcome_filter,
        tool=tool_filter,
        limit=limit,
        offset=offset,
    )
    return DefenseJournalView(
        project_root=str(root),
        generated_at=datetime.now(UTC).isoformat(),
        total=total,
        returned=len(page),
        offset=offset,
        limit=limit,
        truncated=offset + len(page) < total,
        kinds=distinct_kinds(root),
        outcomes=distinct_outcomes(root),
        tools=distinct_tools(root),
        entries=[DefenseEvent(**entry) for entry in page],
    )


@router.get("/defense/journal/export")
def export_defense_journal(
    request: Request,
    project_root: str = "",
    kind: str = Query("", description="Filter by kind"),
    outcome: str = Query("", description="Filter by outcome"),
    tool: str = Query("", description="Filter by tool"),
    fmt: Literal["md", "csv", "json"] = Query("md", alias="format"),
) -> Response:
    """Download the filtered trail as Markdown / CSV / JSON.

    Not paginated: a diagnostic export carries the whole filtered window.
    Read-only, same reader and filters as the page.
    """
    root = _resolve_project(request, project_root)
    kind_filter, outcome_filter = _validate_filters(kind, outcome)
    entries, total = list_defense_events(
        root,
        kind=kind_filter,
        outcome=outcome_filter,
        tool=tool.strip(),
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
                "kinds": distinct_kinds(root),
                "outcomes": distinct_outcomes(root),
                "tools": distinct_tools(root),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        media_type = "application/json"
    elif fmt == "csv":
        body = defense_log_csv(entries, total, len(entries), 0)
        media_type = "text/csv; charset=utf-8"
    else:
        body = render_defense_log_markdown(entries, total, len(entries), 0)
        media_type = "text/markdown; charset=utf-8"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{journal_export_filename(fmt)}"',
            "X-Defense-Total": str(total),
        },
    )


__all__ = ["router"]
