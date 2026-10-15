"""Tests for TeamLifecycleManager CRUD operations with tmp_path fixtures."""

from __future__ import annotations

import importlib
import logging
import time
from pathlib import Path

import pytest

from iterate_harness.swarm import spawn_utils as spawn_utils_mod
from iterate_harness.swarm import team_lifecycle as team_lifecycle_mod
from iterate_harness.swarm.mailbox import validate_team_name
from iterate_harness.swarm.team_lifecycle import (
    TeamFile,
    TeamLifecycleManager,
    TeamMember,
    _destroy_worktree,
    _kill_orphaned_teammate_panes,
    cleanup_team_directories,
    get_team_file_path,
    register_team_for_session_cleanup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """Return a TeamLifecycleManager whose teams live inside tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return TeamLifecycleManager()


def _make_member(agent_id: str = "worker1@alpha", name: str = "worker1") -> TeamMember:
    return TeamMember(
        agent_id=agent_id,
        name=name,
        backend_type="subprocess",
        joined_at=time.time(),
    )


# ---------------------------------------------------------------------------
# TeamMember serialization
# ---------------------------------------------------------------------------


def test_team_member_round_trip():
    member = _make_member()
    data = member.to_dict()
    restored = TeamMember.from_dict(data)
    assert restored.agent_id == member.agent_id
    assert restored.name == member.name
    assert restored.backend_type == member.backend_type
    assert restored.status == "active"


def test_team_member_default_status():
    member = _make_member()
    assert member.status == "active"


# ---------------------------------------------------------------------------
# TeamFile serialization
# ---------------------------------------------------------------------------


def test_team_file_round_trip(tmp_path):
    tf = TeamFile(name="myteam", created_at=time.time(), description="test team")
    path = tmp_path / "team.json"
    tf.save(path)
    loaded = TeamFile.load(path)
    assert loaded.name == "myteam"
    assert loaded.description == "test team"


def test_team_file_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        TeamFile.load(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# TeamLifecycleManager.create_team
# ---------------------------------------------------------------------------


def test_create_team_persists_to_disk(manager):
    tf = manager.create_team("alpha", "first team")
    assert tf.name == "alpha"
    assert tf.description == "first team"

    # Verify it was written to disk
    reloaded = manager.get_team("alpha")
    assert reloaded is not None
    assert reloaded.name == "alpha"


def test_create_team_duplicate_raises(manager):
    manager.create_team("beta")
    with pytest.raises(ValueError, match="already exists"):
        manager.create_team("beta")


def test_team_name_walk_up_rejected(manager, tmp_path):
    """A trailing/embedded ``..`` must not let delete_team rmtree outside the
    teams base directory (agent-driven names are untrusted input)."""
    with pytest.raises(ValueError, match="path-safe"):
        manager.create_team("..")
    with pytest.raises(ValueError, match="path-safe"):
        manager.create_team("../../outside")
    with pytest.raises(ValueError, match="path-safe"):
        manager.create_team("team/sub")
    with pytest.raises(ValueError, match="path-safe"):
        manager.delete_team("../escape")
    assert not (tmp_path / "outside").exists()


def test_validate_team_name_accepts_normal_names():
    assert validate_team_name("alpha")
    assert validate_team_name("my-team-2")


def test_validate_team_name_rejects_empty_and_separators():
    with pytest.raises(ValueError):
        validate_team_name("")
    with pytest.raises(ValueError):
        validate_team_name("a/b")
    with pytest.raises(ValueError):
        validate_team_name("a\\b")
    with pytest.raises(ValueError):
        validate_team_name("..")


# ---------------------------------------------------------------------------
# TeamLifecycleManager.get_team
# ---------------------------------------------------------------------------


def test_get_team_returns_none_for_missing(manager):
    result = manager.get_team("no-such-team")
    assert result is None


def test_get_team_returns_team_file(manager):
    manager.create_team("gamma")
    tf = manager.get_team("gamma")
    assert tf is not None
    assert tf.name == "gamma"


# ---------------------------------------------------------------------------
# TeamLifecycleManager.delete_team
# ---------------------------------------------------------------------------


def test_delete_team_removes_from_disk(manager):
    manager.create_team("to-delete")
    manager.delete_team("to-delete")
    assert manager.get_team("to-delete") is None


def test_delete_nonexistent_team_raises(manager):
    with pytest.raises(ValueError, match="does not exist"):
        manager.delete_team("ghost")


# ---------------------------------------------------------------------------
# TeamLifecycleManager.list_teams
# ---------------------------------------------------------------------------


def test_list_teams_empty_initially(manager):
    teams = manager.list_teams()
    assert teams == []


def test_list_teams_returns_all_sorted(manager):
    for name in ("charlie", "alpha", "bravo"):
        manager.create_team(name)
    teams = manager.list_teams()
    names = [t.name for t in teams]
    assert names == sorted(names)
    assert set(names) == {"alpha", "bravo", "charlie"}


# ---------------------------------------------------------------------------
# TeamLifecycleManager.add_member / remove_member
# ---------------------------------------------------------------------------


def test_add_member_persists(manager):
    manager.create_team("delta")
    member = _make_member()
    updated = manager.add_member("delta", member)
    assert member.agent_id in updated.members

    reloaded = manager.get_team("delta")
    assert reloaded is not None
    assert member.agent_id in reloaded.members


def test_add_member_replaces_existing(manager):
    manager.create_team("epsilon")
    m1 = TeamMember(
        agent_id="w@epsilon", name="old", backend_type="subprocess", joined_at=1.0
    )
    manager.add_member("epsilon", m1)

    m2 = TeamMember(
        agent_id="w@epsilon", name="new", backend_type="in_process", joined_at=2.0
    )
    updated = manager.add_member("epsilon", m2)
    assert updated.members["w@epsilon"].name == "new"


def test_remove_member(manager):
    manager.create_team("zeta")
    member = _make_member("x@zeta", "x")
    manager.add_member("zeta", member)
    updated = manager.remove_member("zeta", "x@zeta")
    assert "x@zeta" not in updated.members


def test_remove_nonexistent_member_raises(manager):
    manager.create_team("eta")
    with pytest.raises(ValueError, match="not a member"):
        manager.remove_member("eta", "ghost@eta")


def test_add_member_to_nonexistent_team_raises(manager):
    with pytest.raises(ValueError, match="does not exist"):
        manager.add_member("no-team", _make_member())


# ---------------------------------------------------------------------------
# _kill_orphaned_teammate_panes — logging when backend lacks kill_pane
# ---------------------------------------------------------------------------


class _NoKillPaneExecutor:
    """A TeammateExecutor that is NOT a PaneBackend (no ``kill_pane`` method).
    Used to prove _kill_one logs a warning instead of silently swallowing an
    AttributeError."""

    type = "tmux"

    def is_available(self):
        return True


async def test_kill_orphaned_pane_logs_when_backend_has_no_kill_pane(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    tf = TeamFile(name="zulu", created_at=time.time())
    tf.members["w@zulu"] = TeamMember(
        agent_id="w@zulu",
        name="w",
        backend_type="tmux",
        joined_at=time.time(),
        tmux_pane_id="%1",
    )
    tf.save(get_team_file_path("zulu"))

    class FakeRegistry:
        def get_executor(self, backend):
            return _NoKillPaneExecutor()

    # NOTE: test_imports.py pops every ``iterate_harness.swarm.*`` module from
    # sys.modules during the suite run, which under pytest's importlib-mode
    # orphans the module object referenced by the module-level ``registry_mod``
    # import. Re-import here in the test body so we patch the *live* registry
    # module that ``_kill_orphaned_teammate_panes`` will resolve at runtime.
    live_registry = importlib.import_module("iterate_harness.swarm.registry")
    monkeypatch.setattr(live_registry, "get_backend_registry", lambda: FakeRegistry())
    monkeypatch.setattr(spawn_utils_mod, "is_inside_tmux", lambda: False)

    with caplog.at_level(logging.WARNING, logger="iterate_harness.swarm.team_lifecycle"):
        await _kill_orphaned_teammate_panes("zulu")

    assert any(
        "does not implement PaneBackend.kill_pane" in m for m in caplog.messages
    ), "expected a warning about the backend lacking kill_pane"


# ---------------------------------------------------------------------------
# _destroy_worktree / cleanup_team_directories — teardown containment
# ---------------------------------------------------------------------------


async def test_destroy_worktree_never_raises_on_git_failure(tmp_path, monkeypatch):
    """Teardown runs mid-shutdown: a failing ``git worktree remove`` (or an
    unreadable/binary ``.git`` marker) must never propagate — the directory is
    still removed via the rmtree fallback."""
    import subprocess as _subprocess

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /nonexistent/main/.git/worktrees/x\n", encoding="utf-8")
    (wt / "file.txt").write_text("work", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise _subprocess.SubprocessError("git exploded")

    monkeypatch.setattr(_subprocess, "run", _boom)

    await _destroy_worktree(str(wt))  # must not raise
    assert not wt.exists()


async def test_destroy_worktree_handles_binary_git_marker(tmp_path):
    """A binary (non-UTF-8) ``.git`` marker must not crash teardown; the
    directory is removed via the rmtree fallback."""
    wt = tmp_path / "wt-binary"
    wt.mkdir()
    (wt / ".git").write_bytes(b"\xff\xfe\x00gitdir: garbage\x00")
    (wt / "file.txt").write_text("work", encoding="utf-8")

    await _destroy_worktree(str(wt))  # must not raise
    assert not wt.exists()


async def test_cleanup_team_directories_removes_team_even_if_worktree_fails(
    tmp_path, monkeypatch
):
    """A failure while tearing down one member's worktree must not leave the
    team directory behind (later passes would treat the half-torn-down team as
    live) and must still unregister the team from session-cleanup tracking."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    tf = TeamFile(name="cleanup-team", created_at=time.time())
    tf.members["w@cleanup-team"] = TeamMember(
        agent_id="w@cleanup-team",
        name="w",
        backend_type="subprocess",
        joined_at=time.time(),
        worktree_path=str(tmp_path / "some-worktree"),
    )
    tf.save(get_team_file_path("cleanup-team"))
    register_team_for_session_cleanup("cleanup-team")

    async def _explode(_path):
        raise RuntimeError("teardown blew up")

    # Patch the collection-time module object: ``test_imports.py`` pops the
    # swarm modules from ``sys.modules``, so a string-target patch would
    # re-import a *fresh* module while ``cleanup_team_directories`` still
    # resolves ``_destroy_worktree`` in the original module's globals.
    monkeypatch.setattr(team_lifecycle_mod, "_destroy_worktree", _explode)

    with pytest.raises(RuntimeError, match="teardown blew up"):
        await cleanup_team_directories("cleanup-team")

    # Deterministic cleanup: team dir gone, registration dropped, even though
    # the worktree teardown failed.
    team_dir = tmp_path / ".iterate-harness" / "teams" / "cleanup-team"
    assert not team_dir.exists()
    from iterate_harness.swarm import team_lifecycle as tl_mod

    assert "cleanup-team" not in tl_mod._session_created_teams


async def test_cleanup_team_directories_tolerates_unreadable_team_file(
    tmp_path, monkeypatch
):
    """An unreadable team.json during cleanup must not raise; the team dir is
    still removed."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    team_dir = tmp_path / ".iterate-harness" / "teams" / "broken-team"
    team_dir.mkdir(parents=True)
    (team_dir / "team.json").write_text("{not json", encoding="utf-8")
    register_team_for_session_cleanup("broken-team")

    await cleanup_team_directories("broken-team")  # must not raise

    assert not team_dir.exists()
    from iterate_harness.swarm import team_lifecycle as tl_mod

    assert "broken-team" not in tl_mod._session_created_teams
