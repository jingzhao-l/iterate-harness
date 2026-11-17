"""Permission policy route (design §17.3 P16).

The engine has always enforced a layered permission policy, but the operator
could only observe it as the outcome of a live tool call: a call that asked for
confirmation or was refused explained itself once, in a run they had to watch.
This route exposes the merged policy (mode, allow/deny lists, path and command
rules, sensitive paths, risk areas, forbidden patterns) plus a decision
simulator that runs the engine's own checker over a hypothetical call.

Read-only: it never writes settings, executes a tool, or contacts a provider.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..roots import resolve_project_root
from ..schemas import PermissionCheckView, PermissionsView

router = APIRouter(tags=["permissions"])

#: Modes a caller may simulate. An empty value means "configured mode".
_VALID_MODES = {"default", "plan", "full_auto"}


@router.get("/permissions", response_model=PermissionsView)
def get_permissions(request: Request, project_root: str = "") -> PermissionsView:
    """Return the effective permission policy for the selected project.

    ``project_root`` follows the same allow-list as every other route. The
    response includes the boundaries merged by ``build_permission_checker``
    (iterate protected paths / risk areas / forbidden patterns), so the page
    shows what is enforced rather than what the settings file happens to say.
    """
    from ...permissions.inventory import build_permission_policy

    root = resolve_project_root(request, project_root)
    return PermissionsView(**build_permission_policy(root))


@router.get("/permissions/check", response_model=PermissionCheckView)
def check_permission(
    request: Request,
    tool: str,
    project_root: str = "",
    path: str = "",
    command: str = "",
    content: str = "",
    read_only: bool | None = None,
    mode: str = "",
) -> PermissionCheckView:
    """Simulate one tool call against the real permission checker.

    ``read_only`` omitted means "infer from the registered tool"; an unknown
    tool is treated as mutating (fail-closed). ``mode`` simulates a different
    *configured* mode (``plan`` / ``default`` / ``full_auto``); an unknown value
    is a 422 rather than a silently ignored parameter.
    """
    from ...permissions.inventory import simulate_permission_decision

    normalized_mode = mode.strip().lower()
    if normalized_mode and normalized_mode not in _VALID_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown mode {mode!r}; expected one of "
                f"{', '.join(sorted(_VALID_MODES))}"
            ),
        )
    if not tool.strip():
        raise HTTPException(status_code=422, detail="tool is required")

    root = resolve_project_root(request, project_root)
    try:
        decision = simulate_permission_decision(
            tool,
            project_root=root,
            file_path=path,
            command=command,
            content=content,
            read_only=read_only,
            mode=normalized_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PermissionCheckView(**decision)


__all__ = ["router"]
