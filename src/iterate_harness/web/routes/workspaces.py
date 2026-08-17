"""Workspaces route (design §17.3 P4).

Surfaces the project's workspace layout: the **primary** checkout (the
project root the WebUI operates on) plus every **isolate worktree** created
by :mod:`~iterate_harness.iterate.worktree_runtime` when ``worktree_isolation``
is enabled. Reads are best-effort (git metadata never raises); the one
mutating operation — removing a stale worktree — goes through the audit log
and requires an explicit ``confirm=true`` (the frontend shows a secondary
confirmation dialog before sending it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ..security import AuditLog
from ..schemas import OperationResult, WorkspaceRemoveRequest, WorkspaceView

router = APIRouter(tags=["workspaces"])


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


def _round_from_slug(slug: str) -> int:
    """Extract the round number from ``iterate-round-N`` (defaults to 0)."""
    marker = "round-"
    if marker not in slug:
        return 0
    tail = slug.split(marker, 1)[1]
    try:
        return max(0, int(tail))
    except ValueError:
        return 0


def _git_info(root: Path) -> dict[str, Any]:
    """Best-effort git metadata for a checkout (never raises).

    Returns ``{"gitRoot", "branch", "head", "dirty"}``; absent fields are
    ``None`` / ``False`` when the directory is not a git repository or git
    is unavailable (the Workspaces page degrades gracefully either way).
    """
    from ...iterate.git_scope import _run_git, detect_repo_root

    info: dict[str, Any] = {"gitRoot": None, "branch": None, "head": None, "dirty": False}
    repo_root = detect_repo_root(root)
    if repo_root is None:
        return info
    info["gitRoot"] = str(repo_root)
    branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        info["branch"] = branch.strip()
    head = _run_git(repo_root, "rev-parse", "--short", "HEAD")
    if head:
        info["head"] = head.strip()
    porcelain = _run_git(repo_root, "status", "--porcelain")
    info["dirty"] = bool(porcelain and porcelain.strip())
    return info


def _primary_workspace(root: Path) -> WorkspaceView:
    """Build the primary-checkout workspace view with config + git context."""
    from ...iterate.config_loader import load_effective_config
    from ...iterate.decision_log import read_entries

    try:
        effective = load_effective_config(root)
        isolation_enabled = bool(effective.config.worktree_isolation)
    except Exception:  # noqa: BLE001 - config read is best-effort
        isolation_enabled = False
    try:
        entry_count = len(read_entries(root))
    except Exception:  # noqa: BLE001 - log read is best-effort
        entry_count = 0

    detail: dict[str, Any] = {
        "slug": "main",
        "isolationEnabled": isolation_enabled,
        "entryCount": entry_count,
        "configExists": (root / "iterate.config.yaml").is_file(),
    }
    detail.update(_git_info(root))
    return WorkspaceView(
        name="main",
        path=str(root.resolve()),
        kind="primary",
        active=True,
        detail=detail,
    )


def _worktree_view(info: Any, root: Path) -> WorkspaceView:
    """Build a workspace view for one isolate worktree."""
    try:
        original = Path(info.original_path).resolve()
    except (OSError, ValueError):
        original = Path(info.original_path)
    belongs_to_project = original == root.resolve()
    return WorkspaceView(
        name=info.slug,
        path=str(info.path),
        kind="worktree",
        active=belongs_to_project,
        detail={
            "slug": info.slug,
            "branch": info.branch,
            "agent_id": info.agent_id or "",
            "created_at": info.created_at,
            "round": _round_from_slug(info.slug),
        },
    )


@router.get("/workspaces", response_model=list[WorkspaceView])
async def list_workspaces(
    project_root: str = "",
) -> list[WorkspaceView]:
    """List the primary checkout plus every isolate worktree of the project.

    Worktrees are read from :class:`~iterate_harness.swarm.worktree.WorktreeManager`;
    only worktrees whose ``original_path`` matches the project root are
    listed, so concurrent projects' slots stay out of view.
    """
    from ...swarm.worktree import WorktreeManager

    root = _resolve_project(project_root)
    views: list[WorkspaceView] = [_primary_workspace(root)]

    mgr = WorktreeManager()
    try:
        worktrees = await mgr.list_worktrees()
    except Exception as exc:  # noqa: BLE001 - listing is best-effort
        raise HTTPException(status_code=500, detail=f"Failed to list worktrees: {exc}") from exc

    # Only worktrees that belong to this project are shown; a worktree is
    # "stale" (removable) when a newer round of the same project exists, so
    # the latest active round's sandbox is never deleted mid-run.
    project_worktrees: list[WorkspaceView] = []
    for info in worktrees:
        view = _worktree_view(info, root)
        if view.active:
            project_worktrees.append(view)
    max_round = max((w.detail.get("round") or 0) for w in project_worktrees) if project_worktrees else 0
    for view in project_worktrees:
        view.detail["stale"] = (view.detail.get("round") or 0) < max_round
    views.extend(project_worktrees)
    return views


@router.post("/workspaces/remove", response_model=OperationResult)
async def remove_workspace(
    body: WorkspaceRemoveRequest,
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Remove a stale isolate worktree (mutating, audited).

    The slug is validated with :func:`~iterate_harness.swarm.worktree.validate_worktree_slug`
    (rejects path traversal / malformed slugs) before the removal runs.
    Requires ``confirm=true``.
    """
    from ...swarm.worktree import WorktreeManager, validate_worktree_slug

    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="remove requires confirm=true (secondary confirmation)",
        )
    slug = body.slug.strip()
    try:
        validate_worktree_slug(slug)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    mgr = WorktreeManager()
    removed = await mgr.remove_worktree(slug, repo_path=root)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No worktree found for slug: {slug}")

    AuditLog(root).record("workspace.remove", slug)
    return OperationResult(
        status="ok",
        message=f"Worktree removed: {slug}",
        target=slug,
    )


__all__ = ["router"]