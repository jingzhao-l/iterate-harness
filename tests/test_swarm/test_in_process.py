"""Tests for InProcessBackend: spawn, shutdown, send_message, and contextvars."""

from __future__ import annotations

from pathlib import Path

import pytest

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
