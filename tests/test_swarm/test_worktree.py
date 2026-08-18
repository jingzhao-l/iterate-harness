"""Tests for validate_worktree_slug edge cases and WorktreeManager helpers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from iterate_harness.swarm import worktree as worktree_mod
from iterate_harness.swarm.worktree import (
    WorktreeManager,
    _flatten_slug,
    _worktree_branch,
    validate_worktree_slug,
)


# ---------------------------------------------------------------------------
# validate_worktree_slug — valid cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "simple",
        "with-dashes",
        "with_underscores",
        "alpha123",
        "a.b.c",
        "feature/my-task",
        "a/b/c",
        "A-Z_0-9.mixed",
        "x" * 64,  # exactly 64 chars
    ],
)
def test_validate_worktree_slug_valid(slug):
    assert validate_worktree_slug(slug) == slug


# ---------------------------------------------------------------------------
# validate_worktree_slug — invalid cases
# ---------------------------------------------------------------------------


def test_validate_empty_slug_raises():
    with pytest.raises(ValueError, match="empty"):
        validate_worktree_slug("")


def test_validate_too_long_slug_raises():
    with pytest.raises(ValueError, match="64"):
        validate_worktree_slug("x" * 65)


def test_validate_absolute_path_raises():
    with pytest.raises(ValueError, match="absolute"):
        validate_worktree_slug("/absolute/path")


def test_validate_backslash_absolute_raises():
    with pytest.raises(ValueError, match="absolute"):
        validate_worktree_slug("\\windows\\path")


def test_validate_dot_segment_raises():
    with pytest.raises(ValueError, match=r"\.|\.\."):
        validate_worktree_slug("a/./b")


def test_validate_dotdot_segment_raises():
    with pytest.raises(ValueError, match=r"\.|\.\."):
        validate_worktree_slug("a/../b")


def test_validate_invalid_chars_raises():
    with pytest.raises(ValueError):
        validate_worktree_slug("has space")


def test_validate_empty_segment_via_double_slash_raises():
    with pytest.raises(ValueError):
        validate_worktree_slug("a//b")


@pytest.mark.parametrize(
    "slug",
    [
        "has space",
        "has@symbol",
        "has!bang",
        "has$dollar",
        "has#hash",
        "has%percent",
    ],
)
def test_validate_various_invalid_chars(slug):
    with pytest.raises(ValueError):
        validate_worktree_slug(slug)


# ---------------------------------------------------------------------------
# _flatten_slug
# ---------------------------------------------------------------------------


def test_flatten_slug_replaces_slash_with_plus():
    assert _flatten_slug("feature/my-task") == "feature+my-task"


def test_flatten_slug_no_slash_unchanged():
    assert _flatten_slug("simple") == "simple"


def test_flatten_slug_multiple_slashes():
    assert _flatten_slug("a/b/c") == "a+b+c"


# ---------------------------------------------------------------------------
# _worktree_branch
# ---------------------------------------------------------------------------


def test_worktree_branch_simple():
    assert _worktree_branch("fix-bug") == "worktree-fix-bug"


def test_worktree_branch_with_slash():
    assert _worktree_branch("feature/foo") == "worktree-feature+foo"


def test_worktree_branch_prefix():
    branch = _worktree_branch("anything")
    assert branch.startswith("worktree-")


# ---------------------------------------------------------------------------
# Worktree metadata persistence / cleanup
# ---------------------------------------------------------------------------


def _make_fake_git(repo_path: Path):
    """Return a _run_git stand-in that simulates a worktree leaf whenever the
    target directory holds a ``.git`` file (mirrors real workflow: a valid
    worktree is one created by WorktreeManager via ``git worktree add``)."""

    async def fake_run_git(*args, cwd=None):
        cwd_path = Path(cwd) if cwd else None
        if args[:2] == ("rev-parse", "--git-dir"):
            # Real `git worktree add` leaves a `.git` file in the worktree dir.
            leaf = cwd_path is not None and (cwd_path / ".git").exists()
            return (0, "", "") if leaf else (128, "", "not a repository")
        if args[:2] == ("rev-parse", "--git-common-dir"):
            return (0, str(repo_path), "")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return (0, cwd_path.name + "-branch", "")
        if args[:2] == ("worktree", "add"):
            i = args.index("-B")
            target = Path(args[i + 2])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").write_text("gitdir: stub\n", encoding="utf-8")
            return (0, "", "")
        if args[:2] == ("worktree", "remove"):
            # Real `git worktree remove --force <path>` deletes the directory.
            target = Path(args[-1])
            if target.exists():
                shutil.rmtree(target)
            return (0, "", "")
        return (0, "", "")

    return fake_run_git


async def test_create_worktree_writes_metadata_and_list_reads_agent_id(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    monkeypatch.setattr(worktree_mod, "_run_git", _make_fake_git(repo))

    await mgr.create_worktree(repo, "task-one", agent_id="alice@alpha")

    # Metadata is persisted as a sidecar OUTSIDE the worktree so it never
    # pollutes the worktree's git state (would otherwise break `git add -A`).
    listed = await mgr.list_worktrees()
    assert len(listed) == 1
    info = listed[0]
    assert info.agent_id == "alice@alpha"
    assert info.slug == "task-one"
    sidecar_path = worktree_mod._sidecar_meta_path(
        mgr.base_dir, info.path.parent.name, info.path.name
    )
    assert sidecar_path.exists()
    assert not (info.path / worktree_mod._WORKTREE_META_FILENAME).exists()
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert data["agent_id"] == "alice@alpha"
    assert data["slug"] == "task-one"


async def test_cleanup_stale_removes_inactive_keeps_active(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    monkeypatch.setattr(worktree_mod, "_run_git", _make_fake_git(repo))

    await mgr.create_worktree(repo, "active-task", agent_id="bob@beta")
    await mgr.create_worktree(repo, "stale-task", agent_id="idle@gamma")

    # Stale worktree pruned; active one retained.
    removed = await mgr.cleanup_stale(active_agent_ids={"bob@beta"})
    assert "stale-task" in removed
    assert "active-task" not in removed

    remaining = await mgr.list_worktrees()
    slugs = [r.slug for r in remaining]
    assert "active-task" in slugs
    assert "stale-task" not in slugs


async def test_cleanup_stale_removes_all_when_no_active_set(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    monkeypatch.setattr(worktree_mod, "_run_git", _make_fake_git(repo))

    await mgr.create_worktree(repo, "only-task", agent_id="carol@delta")

    # active_agent_ids=None → all worktrees with an agent_id are stale.
    removed = await mgr.cleanup_stale(active_agent_ids=None)
    assert "only-task" in removed
