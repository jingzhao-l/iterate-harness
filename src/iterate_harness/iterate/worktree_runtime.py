"""Engine-side worktree isolation runtime for normal-mode iterate loops.

The swarm layer already isolates review agents behind per-agent worktrees
(design §11.3.2 finding #7). This module completes the finding by isolating
the FIX rounds of the MAIN loop: when ``worktree_isolation`` is enabled
(harness ``IterateSettings.worktree_isolation`` or project
``iterate.config.yaml: worktree_isolation``) and a normal-mode loop is
active, the engine swaps the loop's working directory to a dedicated
``iterate/round-N`` git worktree so concurrent sessions never write the same
files.

Lifecycle (driven from ``engine/query.py``):

- :func:`enter_for_round` — on the first normal-mode
  :class:`~iterate_harness.engine.stream_events.ReviewProgressEvent`, create
  an isolated worktree from the current HEAD, copy the (typically gitignored)
  project state (``iterate.config.yaml`` + ``.iterate/``) so the semantic
  tools keep working, then swap ``context.cwd`` so the next round's fix
  writes and validation land in the worktree.
- :func:`resume_if_needed` — at the start of ``run_query``, resume an active
  session from ``tool_metadata`` (survives ``continue_pending`` across
  submit boundaries).
- :func:`finalize` — on a successful stop, commit the fix batch and
  fast-forward-merge it back into the main checkout (with a merge-commit
  fallback when a concurrent session moved the main branch); on an abnormal
  stop (user-forced stop / engine error) drop the worktree entirely — the
  deterministic auto-rollback path.

Notes:

- Fixes in round 1 land in the main checkout (the loop mode is only known
  after the first aggregate) — isolation deterministically covers every
  round after the first progress event.
- Worktree isolation applies to committed state; untracked source files are
  not copied into the worktree.
- All git failures are caught and logged (never crash the engine); a non-git
  repo simply keeps the normal single-workspace behavior.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import worktree_flow

log = logging.getLogger(__name__)

#: Durable metadata key holding the active WorktreeSession payload.
METADATA_KEY = "iterate_worktree_session"

#: File/dir names copied between the main checkout and the worktree so the
#: loop's semantic tools (config + decision log + report) keep working even
#: when these are gitignored / untracked.
_PROJECT_STATE_NAMES = ("iterate.config.yaml", ".iterate")

#: Default commit message prefix for a merged fix round.
_MERGE_MESSAGE = "iterate: merge fix round {round_number}"


def isolation_enabled(policy: object) -> bool:
    """Return True when the attached loop policy requests worktree isolation."""
    return bool(getattr(policy, "worktree_isolation", False))


def active_session(context: object) -> worktree_flow.WorktreeSession | None:
    """Return the active worktree session from the context's tool metadata."""
    tool_metadata = getattr(context, "tool_metadata", None)
    if not isinstance(tool_metadata, dict):
        return None
    return worktree_flow.deserialize_session(tool_metadata.get(METADATA_KEY))


def _sync_project_state(src: Path, dst: Path) -> None:
    """Copy gitignored/untracked project state from *src* into *dst*.

    Best effort: file/dir copy failures are logged, never raised — isolation
    must not break the loop because of a missing config.
    """
    for name in _PROJECT_STATE_NAMES:
        source = src / name
        target = dst / name
        if source.is_file():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except OSError as exc:
                log.warning("iterate worktree: copy %s failed: %s", source, exc)
        elif source.is_dir():
            try:
                shutil.copytree(source, target, dirs_exist_ok=True)
            except OSError as exc:
                log.warning("iterate worktree: copy %s failed: %s", source, exc)


async def enter_for_round(
    context: object,
    progress: object,
) -> worktree_flow.WorktreeSession | None:
    """Enter an isolated worktree for a normal-mode fix round.

    Returns the new session (``context.cwd`` is then pointed at the worktree
    and the session persisted to metadata), or ``None`` when isolation is
    disabled / already active / the mode is not ``normal`` / the repo is not
    git-able.
    """
    if not isolation_enabled(getattr(context, "iterate_policy", None)):
        return None
    if getattr(progress, "mode", "") != "normal":
        return None
    if active_session(context) is not None:
        return None
    repo_path = getattr(context, "cwd", None)
    if repo_path is None:
        return None
    repo_path = Path(repo_path).resolve()
    round_number = int(getattr(progress, "round", 0) or 0)
    try:
        session = await worktree_flow.enter(
            repo_path,
            round_number=round_number,
            target_branch="HEAD",  # base the fix branch on the CURRENT state
        )
    except Exception as exc:  # noqa: BLE001 - isolation is best-effort
        log.warning(
            "iterate worktree isolation disabled for this run (enter failed): %s", exc
        )
        return None

    # Sync gitignored/untracked project state so the semantic tools keep
    # reading/writing the same config + decision log inside the worktree.
    _sync_project_state(repo_path, session.worktree_path)

    # Point every subsequent tool call (fix writes + validation) at the
    # isolated workspace.
    context.cwd = session.worktree_path  # type: ignore[attr-defined]
    tool_metadata = getattr(context, "tool_metadata", None)
    if isinstance(tool_metadata, dict):
        tool_metadata[METADATA_KEY] = worktree_flow.serialize_session(session)
    log.info(
        "iterate worktree isolation active: branch=%s path=%s",
        session.branch,
        session.worktree_path,
    )
    return session


async def resume_if_needed(context: object) -> None:
    """Resume an active worktree session from metadata (cross-call durable).

    Called at the start of ``run_query`` so ``continue_pending`` keeps
    writing inside the worktree. Clears a stale session whose worktree no
    longer exists.
    """
    session = active_session(context)
    if session is None:
        return
    if not session.worktree_path.exists():
        log.warning("iterate worktree %s missing; clearing stale session", session.slug)
        tool_metadata = getattr(context, "tool_metadata", None)
        if isinstance(tool_metadata, dict):
            tool_metadata.pop(METADATA_KEY, None)
        return
    context.cwd = session.worktree_path  # type: ignore[attr-defined]


async def finalize(context: object, *, merged: bool) -> None:
    """Finish an isolated fix session.

    - ``merged=True`` (normal stop): commit the fix batch on the isolated
      branch, sync project state back, fast-forward-merge into the main
      checkout (merge-commit fallback on divergence).
    - ``merged=False`` (abnormal stop / rollback): drop the worktree without
      merging — the deterministic "失败即弃" path.

    Always restores ``context.cwd`` to the main checkout and clears the
    metadata entry. Never raises (failures are logged).
    """
    session = active_session(context)
    if session is None:
        return
    try:
        if merged and session.worktree_path.exists():
            round_number = _round_from_slug(session.slug)
            await worktree_flow.commit(
                session, _MERGE_MESSAGE.format(round_number=round_number)
            )
            _sync_project_state(session.worktree_path, session.repo_path)
        await worktree_flow.exit_session(session, merged=merged)
    except Exception as exc:  # noqa: BLE001 - finalize must never crash the engine
        log.warning("iterate worktree finalize failed: %s", exc)
    finally:
        context.cwd = session.repo_path  # type: ignore[attr-defined]
        tool_metadata = getattr(context, "tool_metadata", None)
        if isinstance(tool_metadata, dict):
            tool_metadata.pop(METADATA_KEY, None)


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
