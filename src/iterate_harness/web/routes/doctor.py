"""Environment self-check route (design §17.3 P11).

``ih doctor`` existed only in the terminal (and the REPL's ``/doctor`` slash
command). This route exposes the same report as fields so the console can
answer "is this machine ready to iterate?" without leaving the browser, and a
CI job could even gate on ``GET /api/v1/doctor``. Read-only: it never writes
anything except the probe files it immediately deletes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..security import allowed_roots, default_root, root_is_allowed
from ..schemas import DoctorView

router = APIRouter(tags=["doctor"])


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


@router.get("/doctor", response_model=DoctorView)
def get_doctor(
    project_root: str = "",
) -> DoctorView:
    """Run the environment self-check for the current project.

    Returns the full check list with an actionable ``remedy`` per failure, so
    the page can render "what is broken and what to do about it" instead of a
    stack trace. Never performs network I/O; never exposes credentials.
    """
    from ...doctor import run_doctor

    root = _resolve_project(project_root)
    report = run_doctor(root)
    return DoctorView(
        project_root=report.project_root,
        generated_at=report.generated_at,
        ok=report.ok,
        has_failures=report.has_failures,
        has_warnings=report.has_warnings,
        summary=report.summary,
        checks=[check.to_dict() for check in report.checks],
    )


__all__ = ["router"]
