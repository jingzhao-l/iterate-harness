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
    registered: set[Path] = set()

    async def fake_run_git(*args, cwd=None):
        cwd_path = Path(cwd) if cwd else None
        if args[:2] == ("rev-parse", "--git-dir"):
            # Real `git worktree add` leaves a `.git` file in the worktree dir.
            leaf = cwd_path is not None and (cwd_path / ".git").exists()
            return (0, "", "") if leaf else (128, "", "not a repository")
        if args[:2] == ("rev-parse", "--git-common-dir"):
            return (0, str(repo_path / ".git"), "")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return (0, cwd_path.name + "-branch", "")
        if args[:2] == ("worktree", "list"):
            return (0, "\n".join(f"worktree {path}" for path in sorted(registered)), "")
        if args[:2] == ("worktree", "add"):
            i = args.index("-B")
            target = Path(args[i + 2])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").write_text("gitdir: stub\n", encoding="utf-8")
            registered.add(target.resolve())
            return (0, "", "")
        if args[:2] == ("worktree", "remove"):
            # Real `git worktree remove --force <path>` deletes the directory.
            target = Path(args[-1])
            registered.discard(target.resolve())
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


# ---------------------------------------------------------------------------
# Defensive fix regressions: stale-dir cleanup + cross-repo removal guards
# ---------------------------------------------------------------------------


def _make_fake_git_for_defects(*repo_paths: Path):
    """Fake git that mirrors real ``git worktree`` registration for 1+ repos.

    A worktree's ``.git`` marker resolves into its owning repo's
    ``.git/worktrees`` metadata; ``rev-parse --git-dir`` fails when the marker
    target is missing (a crashed/aborted run), which is the condition the
    harness must recover from.  ``worktree list --porcelain`` reports only the
    worktrees registered under the queried repo.
    """
    repo_roots = {Path(path).resolve(): Path(path).resolve() / ".git" for path in repo_paths}
    owner_of: dict[Path, Path] = {}

    async def fake_run_git(*args, cwd=None):
        cwd_path = Path(cwd).resolve() if cwd else None
        if cwd_path is None:
            return (0, "", "")
        if args[:2] == ("rev-parse", "--git-dir"):
            if cwd_path in repo_roots:
                return (0, str(repo_roots[cwd_path]), "")
            marker = cwd_path / ".git"
            if marker.is_dir():
                return (0, str(marker), "")
            if marker.is_file():
                raw = marker.read_text(encoding="utf-8").strip()
                if raw.startswith("gitdir:"):
                    target = Path(raw[len("gitdir:"):].strip())
                    if not target.is_absolute():
                        target = cwd_path / target
                    if target.exists():
                        return (0, str(target.resolve()), "")
            return (128, "", "not a git repository")
        if args[:2] == ("rev-parse", "--git-common-dir"):
            repo = owner_of.get(cwd_path)
            if repo is not None:
                return (0, str(repo_roots[repo]), "")
            return (128, "", "not a worktree")
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return (0, cwd_path.name + "-branch", "")
        if args[:2] == ("worktree", "list"):
            matching = sorted(str(p) for p, owner in owner_of.items() if owner == cwd_path)
            return (0, "\n".join(f"worktree {path}" for path in matching), "")
        if args[:2] == ("worktree", "add"):
            i = args.index("-B")
            target = Path(args[i + 2])
            target.mkdir(parents=True, exist_ok=True)
            wt_meta = repo_roots[cwd_path] / "worktrees" / target.name
            wt_meta.mkdir(parents=True, exist_ok=True)
            (target / ".git").write_text(f"gitdir: {wt_meta}\n", encoding="utf-8")
            owner_of[target.resolve()] = Path(cwd_path).resolve()
            return (0, "", "")
        if args[:2] == ("worktree", "remove"):
            target = Path(args[-1])
            owner_of.pop(target.resolve(), None)
            if target.exists():
                shutil.rmtree(target)
            return (0, "", "")
        return (0, "", "")

    return fake_run_git


async def test_create_worktree_cleans_stale_empty_dir(tmp_path, monkeypatch):
    """A leftover empty dir from a crashed run must not block ``git worktree add``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    monkeypatch.setattr(worktree_mod, "_run_git", _make_fake_git(repo))

    slug = "stale-empty"
    namespace = worktree_mod.repo_namespace(str(repo.resolve()))
    stale_dir = mgr.base_dir / namespace / worktree_mod._flatten_slug(slug)
    stale_dir.mkdir(parents=True)

    await mgr.create_worktree(repo, slug)

    listed = await mgr.list_worktrees()
    assert [info.slug for info in listed] == ["stale-empty"]


async def test_create_worktree_cleans_stale_own_repo_marker(tmp_path, monkeypatch):
    """An unregistered dir whose gitdir marker points into the caller's repo
    (orphaned by a crash before registration) is safely removed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    fake = _make_fake_git_for_defects(repo)
    monkeypatch.setattr(worktree_mod, "_run_git", fake)

    slug = "orphan"
    namespace = worktree_mod.repo_namespace(str(repo.resolve()))
    orphan_dir = mgr.base_dir / namespace / worktree_mod._flatten_slug(slug)
    orphan_dir.mkdir(parents=True)
    (orphan_dir / ".git").write_text(
        f"gitdir: {repo.resolve() / '.git' / 'worktrees' / 'dead'}\n",
        encoding="utf-8",
    )

    info = await mgr.create_worktree(repo, slug)
    assert info.path == orphan_dir
    assert (orphan_dir / ".git").exists()


async def test_create_worktree_refuses_stale_dir_with_foreign_repo_marker(tmp_path, monkeypatch):
    """Never delete a leftover whose gitdir marker points into a DIFFERENT repo."""
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    monkeypatch.setattr(worktree_mod, "_run_git", _make_fake_git_for_defects(repo_a, repo_b))

    slug = "foreign"
    namespace = worktree_mod.repo_namespace(str(repo_a.resolve()))
    foreign_dir = mgr.base_dir / namespace / worktree_mod._flatten_slug(slug)
    foreign_dir.mkdir(parents=True)
    (foreign_dir / ".git").write_text(
        f"gitdir: {repo_b.resolve() / '.git' / 'worktrees' / 'foreign'}\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="could not be verified as stale"):
        await mgr.create_worktree(repo_a, slug)
    assert foreign_dir.exists()


async def test_remove_worktree_refuses_other_repo(tmp_path, monkeypatch):
    """A slug matching a worktree of ANOTHER repository must not be pruned."""
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    fake = _make_fake_git_for_defects(repo_a, repo_b)
    monkeypatch.setattr(worktree_mod, "_run_git", fake)

    await mgr.create_worktree(repo_a, "mine")

    with pytest.raises(RuntimeError, match="not .*repo-b"):
        await mgr.remove_worktree("mine", repo_path=repo_b)
    assert (await mgr.list_worktrees()) != []


async def test_remove_worktree_refuses_ambiguous_slug_across_repos(tmp_path, monkeypatch):
    """Without a repo_path, a slug shared by two repositories is refused."""
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()
    mgr = WorktreeManager(base_dir=tmp_path / "wt")
    fake = _make_fake_git_for_defects(repo_a, repo_b)
    monkeypatch.setattr(worktree_mod, "_run_git", fake)

    await mgr.create_worktree(repo_a, "shared")
    await mgr.create_worktree(repo_b, "shared")

    with pytest.raises(RuntimeError, match="multiple repositories"):
        await mgr.remove_worktree("shared")
    # Neither repository's worktree was deleted.
    assert len(await mgr.list_worktrees()) == 2
