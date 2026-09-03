"""Unit tests for the shared UI/session state model (state/app_state.py)."""

from __future__ import annotations

from iterate_harness.state.app_state import AppState


def _make_state(**overrides) -> AppState:
    defaults = {"model": "claude-sonnet-4-6", "permission_mode": "default", "theme": "default"}
    defaults.update(overrides)
    return AppState(**defaults)


def test_required_fields_are_kept():
    state = _make_state()
    assert state.model == "claude-sonnet-4-6"
    assert state.permission_mode == "default"
    assert state.theme == "default"


def test_defaults():
    state = _make_state()
    assert state.cwd == "."
    assert state.task_mode == "iterate"
    assert state.provider == "unknown"
    assert state.auth_status == "missing"
    assert state.base_url == ""
    assert state.vim_enabled is False
    assert state.voice_enabled is False
    assert state.voice_available is False
    assert state.voice_reason == ""
    assert state.fast_mode is False
    assert state.effort == "medium"
    assert state.passes == 1
    assert state.mcp_connected == 0
    assert state.mcp_failed == 0
    assert state.bridge_sessions == 0
    assert state.output_style == "default"
    assert state.keybindings == {}


def test_fields_are_mutable():
    state = _make_state()
    state.vim_enabled = True
    state.voice_enabled = True
    state.mcp_connected = 3
    state.mcp_failed = 1
    state.bridge_sessions = 2
    state.effort = "high"
    state.passes = 3
    state.output_style = "compact"

    assert state.vim_enabled is True
    assert state.voice_enabled is True
    assert state.mcp_connected == 3
    assert state.mcp_failed == 1
    assert state.bridge_sessions == 2
    assert state.effort == "high"
    assert state.passes == 3
    assert state.output_style == "compact"


def test_custom_initial_values():
    state = _make_state(
        cwd="/tmp/project",
        provider="openai",
        auth_status="configured",
        base_url="https://api.example.com/v1",
        voice_available=True,
        voice_reason="microphone found",
        fast_mode=True,
    )
    assert state.cwd == "/tmp/project"
    assert state.task_mode == "iterate"
    assert state.provider == "openai"
    assert state.auth_status == "configured"
    assert state.base_url == "https://api.example.com/v1"
    assert state.voice_available is True
    assert state.voice_reason == "microphone found"
    assert state.fast_mode is True


def test_task_mode_is_mutable():
    state = _make_state()
    state.task_mode = "code"
    assert state.task_mode == "code"
    state.task_mode = "iterate"
    assert state.task_mode == "iterate"


def test_keybindings_default_factory_is_isolated_per_instance():
    a = _make_state()
    b = _make_state()
    a.keybindings["ctrl+l"] = "clear"
    assert a.keybindings == {"ctrl+l": "clear"}
    assert b.keybindings == {}


def test_preseeded_keybindings_are_kept():
    state = _make_state(keybindings={"ctrl+t": "tasks"})
    assert state.keybindings == {"ctrl+t": "tasks"}
