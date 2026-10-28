"""Recipe-gate route (design §17.3 P9).

The CLI has answered "may the recipe proceed to re-verification?" since the
GlassPane hand-off landed (``ih iterate gate``). Until now the console could
not: the answer lived in the decision log but only the terminal could read it.
This route exposes the same records as fields — the latest answer plus the
history of every round that published one — so the operator can see a refusal
and its remedy without leaving the console. Read-only: it never writes
``.iterate``.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ...iterate.glasspane_recipe import gate_history, render_gate_state
from ..schemas import GateView
from ..security import allowed_roots, default_root, root_is_allowed

router = APIRouter(tags=["gate"])


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


@router.get("/gate", response_model=GateView)
def get_gate(
    project_root: str = "",
    limit: int = Query(100, ge=1, le=500),
) -> GateView:
    """The recipe gate's latest answer plus the rounds that published one.

    ``rendered`` is the exact text ``ih iterate gate`` prints, so the page and
    the terminal can be compared line for line. ``has_gate`` is false only when
    nothing was recorded — that is absence, not a pass, and the page says so.
    """
    root = _resolve_project(project_root)
    history = gate_history(root)
    latest = history[-1] if history else None
    return GateView(
        project_root=str(root),
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        has_gate=latest is not None,
        total=len(history),
        latest=latest.to_dict() if latest is not None else None,
        history=[record.to_dict() for record in history[-limit:]],
        rendered=render_gate_state(root),
    )


__all__ = ["router"]
