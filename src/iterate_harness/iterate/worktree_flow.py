"""Git-isolation orchestration for iterate fix rounds.

Wraps the kernel's :class:`~iterate_harness.swarm.worktree.WorktreeManager`
(the ``~/.iterate-harness/worktrees`` implementation with node_modules/.venv
symlink reuse and stale cleanup — design §11.3.2 finding #7) into the
iterate fix-round flow:

1. ``enter`` — create an isolated worktree + branch before fixes run
2. ``commit`` — commit the fix batch on the isolated branch
3. ``exit(merged=...)`` — merge back on validation success, or drop the
   worktree entirely on validation failure (auto-rollback)

All git operations run through :mod:`subprocess` with a bounded timeout and
structured errors — a failing git call raises :class:`WorktreeFlowError`
with the captured stderr instead of crashing the loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from ..swarm.worktree import WorktreeManager

log = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 60
ITERATE_BRANCH_PREFIX = "iterate"


class WorktreeFlowError(RuntimeError):
    """A git/worktree operation in the iterate fix flow failed."""


@dataclass
class WorktreeSession:
    """One isolated fix-round workspace."""

    repo_path: Path
    slug: str
    branch: str
    worktree_path: Path


def serialize_session(session: WorktreeSession) -> dict[str, str]:
    """Serialize a session for durable storage (e.g. engine tool_metadata)."""
    return {
        "repo_path": str(session.repo_path),
        "slug": session.slug,
        "branch": session.branch,
        "worktree_path": str(session.worktree_path),
    }


def deserialize_session(data: object) -> WorktreeSession | None:
    """Rebuild a :class:`WorktreeSession` from :func:`serialize_session` output.

    Returns ``None`` when the payload is malformed (missing fields / empty
    slug) — callers treat that as "no active worktree session".
    """
    if not isinstance(data, dict):
        return None
    repo_path = data.get("repo_path")
    slug = data.get("slug")
    branch = data.get("branch")
    worktree_path = data.get("worktree_path")
    if not all(isinstance(v, str) and v for v in (repo_path, slug, branch, worktree_path)):
        return None
    return WorktreeSession(
        repo_path=Path(repo_path),
        slug=slug,
        branch=branch,
        worktree_path=Path(worktree_path),
    )


async def _git(args: list[str], cwd: Path) -> str:
    """Run one git command; raise :class:`WorktreeFlowError` on failure."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        raise WorktreeFlowError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise WorktreeFlowError(f"git {' '.join(args)} failed (exit {proc.returncode}): {detail}")
    return stdout.decode("utf-8", errors="replace")


async def enter(
    repo_path: str | Path,
    manager: WorktreeManager | None = None,
    *,
    round_number: int,
    target_branch: str = "main",
) -> WorktreeSession:
    """Create an isolated worktree + ``iterate/*`` branch for a fix round.

    The branch name is deterministic per round (``iterate/round-<n>``) so
    repeated runs are greppable in the decision log and git history.
    """
    repo = Path(repo_path).resolve()
    mgr = manager or WorktreeManager()
    branch = f"{ITERATE_BRANCH_PREFIX}/round-{round_number}"
    slug = branch.replace("/", "-")
    info = await mgr.create_worktree(repo, slug=slug, branch=branch, agent_id=f"iterate-r{round_number}")
    if target_branch and target_branch != "HEAD":
        # Base the fix branch on the configured target branch so merges
        # land where the project expects them.
        try:
            await _git(["checkout", "-B", branch, target_branch], cwd=info.path)
        except WorktreeFlowError:
            # Target branch may not exist as a local ref (e.g. fresh clone
            # on a feature branch) — keep the default base and log it.
            log.warning(
                "iterate worktree %s could not rebase onto %s; keeping default base",
                slug,
                target_branch,
            )
    return WorktreeSession(
        repo_path=repo,
        slug=slug,
        branch=branch,
        worktree_path=Path(info.path),
    )


async def commit(session: WorktreeSession, message: str) -> str | None:
    """Commit all changes inside the isolated worktree; return the commit sha.

    Returns ``None`` when there was nothing to commit (a valid outcome for
    a fix round whose fixes were all rejected by hooks).
    """
    status = await _git(["status", "--porcelain"], cwd=session.worktree_path)
    if not status.strip():
        return None
    await _git(["add", "-A"], cwd=session.worktree_path)
    await _git(["commit", "-m", message, "--no-verify"], cwd=session.worktree_path)
    sha = (await _git(["rev-parse", "HEAD"], cwd=session.worktree_path)).strip()
    return sha or None


async def exit_session(
    session: WorktreeSession,
    manager: WorktreeManager | None = None,
    *,
    merged: bool,
) -> bool:
    """Leave the isolated workspace.

    - ``merged=True``: fast-forward-merge the fix branch into the current
      branch of the main checkout, then remove the worktree.
    - ``merged=False`` (validation failed): drop the worktree WITHOUT
      merging — this is the deterministic auto-rollback path.

    Returns whether the merge was performed.
    """
    mgr = manager or WorktreeManager()
    try:
        if merged:
            await _git(["merge", "--ff-only", session.branch], cwd=session.repo_path)
    finally:
        removed = await mgr.remove_worktree(session.slug)
        if not removed:
            log.warning("iterate worktree %s was not removed (may be stale)", session.slug)
    return merged


async def rollback(session: WorktreeSession, manager: WorktreeManager | None = None) -> None:
    """Explicit rollback alias: exit the session without merging."""
    await exit_session(session, manager, merged=False)
