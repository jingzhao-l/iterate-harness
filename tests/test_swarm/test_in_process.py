"""Tests for InProcessBackend: spawn, shutdown, send_message, and contextvars."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from iterate_harness.swarm import in_process as in_process_mod
from iterate_harness.swarm.in_process import (
    InProcessBackend,
    TeammateContext,
    build_teammate_query_context,
    get_teammate_context,
    set_teammate_context,
)
from iterate_harness.swarm.types import TeammateMessage, TeammateSpawnConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spawn_config():
    return TeammateSpawnConfig(
        name="worker",
        team="test-team",
        prompt="hello",
        cwd="/tmp",
        parent_session_id="sess-001",
    )


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return InProcessBackend()


def spawn_config_for(name: str, team: str) -> TeammateSpawnConfig:
    return TeammateSpawnConfig(
        name=name,
        team=team,
        prompt="hello",
        cwd="/tmp",
        parent_session_id="sess-001",
    )


# ---------------------------------------------------------------------------
# TeammateContext
# ---------------------------------------------------------------------------


def test_teammate_context_defaults():
    ctx = TeammateContext(
        agent_id="w@t",
        agent_name="w",
        team_name="t",
    )
    assert ctx.color is None
    assert ctx.plan_mode_required is False
    assert not ctx.cancel_event.is_set()


# ---------------------------------------------------------------------------
# ContextVar get / set
# ---------------------------------------------------------------------------


def test_get_teammate_context_returns_none_outside_task():
    # Outside any async task, the contextvar should be None
    result = get_teammate_context()
    assert result is None


async def test_set_and_get_teammate_context():
    ctx = TeammateContext(agent_id="x@y", agent_name="x", team_name="y")
    set_teammate_context(ctx)
    assert get_teammate_context() is ctx


# ---------------------------------------------------------------------------
# build_teammate_query_context (real agent wiring, not a stub)
# ---------------------------------------------------------------------------


def test_build_query_context_resolves_full_wiring(monkeypatch, tmp_path):
    """With auth available the teammate gets a real QueryContext carrying the
    spawn model, cwd, tool registry, and permission checker."""

    class _FakeApiClient:
        pass

    monkeypatch.setattr(
        "iterate_harness.ui.runtime._resolve_api_client_from_settings",
        lambda settings: _FakeApiClient(),
    )
    monkeypatch.setattr(
        "iterate_harness.config.settings.Settings.materialize_active_profile",
        lambda self: self,
    )
    config = TeammateSpawnConfig(
        name="worker",
        team="team",
        prompt="do the thing",
        cwd=str(tmp_path),
        parent_session_id="s",
        model="claude-sonnet",
    )
    context = build_teammate_query_context(config)
    assert context is not None
    assert context.model == "claude-sonnet"
    assert str(context.cwd) == str(tmp_path)
    assert context.system_prompt
    assert context.tool_registry.get("bash") is not None
    assert context.max_tokens > 0


def test_build_query_context_applies_allowed_and_denied_tool_posture(monkeypatch, tmp_path):
    """Regression: the spawn config's allowed/denied tool lists used to have
    NO consumer — an in-process teammate declared \u201cread-only\u201d got the full
    registry and the parent's permission settings, so it could still write.
    They must be merged into the teammate's settings and enforced by its
    permission checker."""

    class _FakeApiClient:
        pass

    monkeypatch.setattr(
        "iterate_harness.ui.runtime._resolve_api_client_from_settings",
        lambda settings: _FakeApiClient(),
    )
    monkeypatch.setattr(
        "iterate_harness.config.settings.Settings.materialize_active_profile",
        lambda self: self,
    )
    config = TeammateSpawnConfig(
        name="readonly-worker",
        team="team",
        prompt="inspect only",
        cwd=str(tmp_path),
        parent_session_id="s",
        allowed_tools=["edit_file"],
        denied_tools=["write_file", "bash"],
    )
    context = build_teammate_query_context(config)
    assert context is not None
    checker = context.permission_checker

    # Allow-listed mutating tool is granted immediately (no confirmation),
    # even though the effective mode is not full_auto.
    edit = checker.evaluate("edit_file", is_read_only=False, file_path=str(tmp_path / "a.py"))
    assert edit.allowed is True
    assert "explicitly allowed" in edit.reason
    # Deny-listed tools are blocked outright.
    write = checker.evaluate("write_file", is_read_only=False, file_path=str(tmp_path / "out.txt"))
    assert write.allowed is False
    assert "explicitly denied" in write.reason
    bash = checker.evaluate("bash", is_read_only=False, command="whoami")
    assert bash.allowed is False
    assert "explicitly denied" in bash.reason
    # An ordinary mutating tool that is neither listed nor read-only must NOT
    # silently pass -- the allow-list is a strict scope, not a hint.
    from iterate_harness.tools.task_create_tool import TaskCreateTool

    other = checker.evaluate(
        TaskCreateTool.name, is_read_only=False, command="touch /tmp/leak"
    )
    assert other.allowed is False


def test_build_query_context_task_mode_code_inherits_kernel(monkeypatch, tmp_path):
    class _FakeApiClient:
        pass

    monkeypatch.setattr(
        "iterate_harness.ui.runtime._resolve_api_client_from_settings",
        lambda settings: _FakeApiClient(),
    )
    monkeypatch.setattr(
        "iterate_harness.config.settings.Settings.materialize_active_profile",
        lambda self: self,
    )
    config = TeammateSpawnConfig(
        name="worker",
        team="team",
        prompt="fix it",
        cwd=str(tmp_path),
        parent_session_id="s",
        task_mode="code",
    )
    context = build_teammate_query_context(config)
    assert context is not None
    # A code-mode worker runs the defensive kernel (design §20.5).
    assert context.defensive_kernel is not None


def test_build_query_context_returns_none_without_auth(monkeypatch, tmp_path):
    """Missing auth must degrade to None (stub fallback) instead of raising."""
    import builtins

    def _raise_system_exit(_settings):
        raise SystemExit(1)

    monkeypatch.setattr(
        "iterate_harness.ui.runtime._resolve_api_client_from_settings",
        _raise_system_exit,
    )
    monkeypatch.setattr(
        "iterate_harness.config.settings.Settings.materialize_active_profile",
        lambda self: self,
    )
    config = TeammateSpawnConfig(
        name="worker",
        team="team",
        prompt="run",
        cwd=str(tmp_path),
        parent_session_id="s",
    )
    context = build_teammate_query_context(config)
    assert context is None
    # No stray writes to the real stderr from the failed builder.
    del builtins


# ---------------------------------------------------------------------------
# InProcessBackend.spawn
# ---------------------------------------------------------------------------


async def test_spawn_returns_success_result(backend, spawn_config):
    result = await backend.spawn(spawn_config)
    assert result.success is True
    assert result.agent_id == "worker@test-team"
    assert result.backend_type == "in_process"
    assert result.task_id.startswith("in_process_")


async def test_spawn_duplicate_returns_failure(backend, spawn_config):
    await backend.spawn(spawn_config)
    # Spawn again while first is still running
    result = await backend.spawn(spawn_config)
    assert result.success is False
    assert result.error is not None


async def test_spawn_creates_active_agent(backend, spawn_config):
    await backend.spawn(spawn_config)
    assert backend.is_active("worker@test-team")


# ---------------------------------------------------------------------------
# InProcessBackend.shutdown
# ---------------------------------------------------------------------------


async def test_shutdown_unknown_agent_returns_false(backend):
    result = await backend.shutdown("nonexistent@team")
    assert result is False


async def test_graceful_shutdown(backend, spawn_config):
    await backend.spawn(spawn_config)
    assert backend.is_active("worker@test-team")

    result = await backend.shutdown("worker@test-team", timeout=2.0)
    assert result is True
    assert not backend.is_active("worker@test-team")


async def test_force_shutdown(backend, spawn_config):
    await backend.spawn(spawn_config)
    result = await backend.shutdown("worker@test-team", force=True, timeout=2.0)
    assert result is True


# ---------------------------------------------------------------------------
# InProcessBackend.send_message
# ---------------------------------------------------------------------------


async def test_send_message_writes_to_mailbox(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = TeammateSpawnConfig(
        name="rcvr",
        team="myteam",
        prompt="wait",
        cwd="/tmp",
        parent_session_id="s",
    )
    await backend.spawn(config)

    msg = TeammateMessage(text="work on it", from_agent="leader")
    # Should not raise
    await backend.send_message("rcvr@myteam", msg)

    # Verify the message lands in the mailbox the RECEIVER polls — the one
    # created at spawn with the full agent_id (name@team), not a bare name.
    from iterate_harness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox(team_name="myteam", agent_id="rcvr@myteam")
    messages = await mailbox.read_all(unread_only=False)
    assert any(m.payload.get("content") == "work on it" for m in messages)

    await backend.shutdown("rcvr@myteam", force=True)


async def test_send_message_invalid_agent_id_raises(backend):
    with pytest.raises(ValueError, match="agentName@teamName"):
        await backend.send_message("no-at-sign", TeammateMessage(text="hi", from_agent="l"))


async def test_send_message_non_numeric_timestamp_is_safe(backend, tmp_path, monkeypatch):
    """A non-numeric (e.g. ISO-8601) timestamp must not break delivery; the
    message falls back to time.time() instead of raising ValueError."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = TeammateSpawnConfig(
        name="rcvr", team="myteam", prompt="wait",
        cwd="/tmp", parent_session_id="s",
    )
    await backend.spawn(config)

    msg = TeammateMessage(text="hi", from_agent="leader", timestamp="2026-08-18T00:00:00Z")
    await backend.send_message("rcvr@myteam", msg)  # must not raise

    from iterate_harness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox(team_name="myteam", agent_id="rcvr@myteam")
    messages = await mailbox.read_all(unread_only=False)
    assert any(m.payload.get("content") == "hi" for m in messages)
    for m in messages:
        if m.payload.get("content") == "hi":
            assert isinstance(m.timestamp, float)
            assert m.timestamp > 0  # time.time()-based, not NaN/0
    await backend.shutdown("rcvr@myteam", force=True)


async def test_send_message_numeric_timestamp_preserved(backend, tmp_path, monkeypatch):
    """A numeric timestamp string must round-trip as the same float value."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = TeammateSpawnConfig(
        name="rcvr", team="myteam", prompt="wait",
        cwd="/tmp", parent_session_id="s",
    )
    await backend.spawn(config)

    msg = TeammateMessage(text="hi", from_agent="leader", timestamp="1234567890.5")
    await backend.send_message("rcvr@myteam", msg)

    from iterate_harness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox(team_name="myteam", agent_id="rcvr@myteam")
    messages = await mailbox.read_all(unread_only=False)
    assert any(m.timestamp == 1234567890.5 for m in messages)
    await backend.shutdown("rcvr@myteam", force=True)


# ---------------------------------------------------------------------------
# active_agents / shutdown_all
# ---------------------------------------------------------------------------


async def test_active_agents_lists_running(backend, spawn_config):
    await backend.spawn(spawn_config)
    active = backend.active_agents()
    assert "worker@test-team" in active


async def test_shutdown_all(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("a", "b"):
        cfg = TeammateSpawnConfig(
            name=name,
            team="t",
            prompt="run",
            cwd="/tmp",
            parent_session_id="s",
        )
        await backend.spawn(cfg)

    await backend.shutdown_all(force=True, timeout=2.0)
    assert backend.active_agents() == []


# ---------------------------------------------------------------------------
# Regression: no-query-context teammate must FAIL, not report success
# ---------------------------------------------------------------------------


async def _wait_terminal(manager, task_id, timeout=10.0):
    """Poll the task manager until the record reaches a terminal state."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = manager.get_task(task_id)
        if record is not None and record.status in {"completed", "failed", "killed"}:
            return record
        await asyncio.sleep(0.05)
    return manager.get_task(task_id)


async def test_teammate_without_query_context_records_failure(
    backend, tmp_path, monkeypatch
):
    """Regression: a teammate that cannot build a QueryContext used to run a
    cancel-aware stub and then report ``completed`` — a prompt that was never
    executed masquerading as a successful run. It must be recorded FAILED and
    the leader must be told so."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    # Patch the collection-time module object: ``test_imports.py`` pops the
    # swarm modules from ``sys.modules``, so a string-target patch would
    # re-import a *fresh* module and miss the globals the backend code
    # actually consults at runtime.
    monkeypatch.setattr(in_process_mod, "build_teammate_query_context", lambda config: None)
    result = await backend.spawn(spawn_config_for("worker", "test-team"))

    # Let the teammate hit its no-context failure path (1s cancel-aware window).
    task = backend._active["worker@test-team"].task
    await asyncio.wait([task], timeout=10.0)

    from iterate_harness.tasks import get_task_manager

    manager = get_task_manager()
    record = await _wait_terminal(manager, result.task_id)
    assert record is not None
    assert record.status == "failed"
    assert record.return_code == 1

    # The leader's idle notification carries the failure, not "finished".
    from iterate_harness.swarm.mailbox import TeammateMailbox

    leader_box = TeammateMailbox(team_name="test-team", agent_id="leader")
    messages = await leader_box.read_all(unread_only=False)
    assert any("FAILED" in (m.payload.get("summary") or "") for m in messages)


async def test_graceful_shutdown_of_unrunnable_teammate_marks_killed(
    backend, tmp_path, monkeypatch
):
    """A teammate stopped before it could run its prompt must not be recorded
    as a successful completion."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    # Patch the collection-time module object (see the note in
    # ``test_teammate_without_query_context_records_failure``).
    monkeypatch.setattr(in_process_mod, "build_teammate_query_context", lambda config: None)
    result = await backend.spawn(spawn_config_for("worker", "test-team"))

    assert await backend.shutdown("worker@test-team", timeout=5.0) is True

    from iterate_harness.tasks import get_task_manager

    manager = get_task_manager()
    record = await _wait_terminal(manager, result.task_id)
    assert record is not None
    assert record.status == "killed"


async def test_predecessor_done_callback_does_not_evict_respawned_entry(backend, spawn_config):
    """Race regression: the done-callback of a finished task can fire after a
    respawn replaced ``_active[agent_id]``. It must not unregister the NEW
    teammate's registry entry (which silently dropped it from
    ``active_agents`` / ``list_teammates`` while it was still running)."""
    from iterate_harness.swarm.in_process import TeammateAbortController, _TeammateEntry

    await backend.spawn(spawn_config)
    agent_id = "worker@test-team"
    old_entry = backend._active[agent_id]

    # Simulate the respawn: a fresh entry replaces the old one in the registry.
    new_task = asyncio.create_task(asyncio.sleep(60))
    new_entry = _TeammateEntry(
        task=new_task,
        abort_controller=TeammateAbortController(),
        task_id="in_process_replacement",
    )
    backend._active[agent_id] = new_entry

    # Now the old task dies; its done callback must leave the new entry alone.
    old_entry.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await old_entry.task
    # Yield once so the done-callback (scheduled via call_soon) has run.
    await asyncio.sleep(0)

    assert backend._active.get(agent_id) is new_entry
    assert backend.is_active(agent_id) is True

    new_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await new_task
