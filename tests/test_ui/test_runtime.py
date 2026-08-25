"""Tests for runtime handle_line snapshot semantics."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.commands import CommandResult
from iterate_harness.ui.runtime import RuntimeBundle, handle_line


class _FakeSettings:
    """Settings double carrying only the fields sync_app_state touches."""

    model = "original-model"
    max_turns = 8
    base_url = ""
    permission = SimpleNamespace(mode=SimpleNamespace(value="default"))
    theme = "default"
    vim_mode = False
    voice_mode = False
    fast_mode = False
    effort = "medium"
    passes = 1
    output_style = "default"

    def merge_cli_overrides(self, **kwargs):
        return self


class _FakeEngine:
    def __init__(self) -> None:
        self.model = "original-model"
        self.max_turns = None
        self.messages = []
        self.total_usage = UsageSnapshot(input_tokens=0, output_tokens=0)
        self.tool_metadata = {}
        self.submitted: list[str] = []

    def set_model(self, model: str) -> None:
        self.model = model

    def set_system_prompt(self, _prompt: str) -> None:
        return None

    def set_max_turns(self, turns: int | None) -> None:
        self.max_turns = turns

    async def submit_message(self, prompt: str):
        self.submitted.append(prompt)
        yield None  # keep this an async generator


class _FakeCommand:
    async def handler(self, _args, _context):
        return CommandResult(submit_prompt="continue please", submit_model="fake-submit-model")


class _FakeCommands:
    def lookup(self, _line):
        return (_FakeCommand(), "")


class _FakeAppState:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set(self, **kwargs) -> None:
        self.calls.append(kwargs)

    def get(self):
        return None


class _FakeSessionBackend:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []

    def save_snapshot(self, **kwargs) -> None:
        self.snapshots.append(kwargs)


class _FakeMcpManager:
    def list_statuses(self):
        return []


class _FakeHookRegistry:
    def summary(self) -> str:
        return "fake-hooks"


def _build_bundle(tmp_path: Path) -> tuple[RuntimeBundle, _FakeEngine, _FakeSessionBackend]:
    engine = _FakeEngine()
    backend = _FakeSessionBackend()
    bundle = RuntimeBundle(
        api_client=None,
        cwd=str(tmp_path),
        mcp_manager=_FakeMcpManager(),
        tool_registry=None,
        app_state=_FakeAppState(),
        hook_executor=None,
        engine=engine,
        commands=_FakeCommands(),
        external_api_client=True,
        enforce_max_turns=False,
        session_id="session-1",
        session_backend=backend,
    )
    return bundle, engine, backend


@pytest.mark.asyncio
async def test_handle_line_snapshots_submission_model_not_restored_model(tmp_path: Path, monkeypatch):
    """Regression: the finally block restored the original model *before*
    save_snapshot ran, so the snapshot recorded the wrong (restored) model.
    The snapshot must record the model the submitted turn actually ran under."""
    bundle, engine, backend = _build_bundle(tmp_path)

    monkeypatch.setattr(
        "iterate_harness.ui.runtime.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.load_hook_registry",
        lambda _settings, _plugins: _FakeHookRegistry(),
    )
    monkeypatch.setattr("iterate_harness.ui.runtime.load_plugins", lambda *a, **k: [])
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.build_runtime_system_prompt",
        lambda *args, **kwargs: "system-prompt",
    )
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.detect_provider",
        lambda _settings: SimpleNamespace(name="fake-provider", voice_supported=False, voice_reason=None),
    )
    monkeypatch.setattr("iterate_harness.ui.runtime.auth_status", lambda _settings: "ok")
    monkeypatch.setattr("iterate_harness.ui.runtime.load_keybindings", lambda: {})

    events: list[object] = []

    async def _print_system(_message: str) -> None:
        return None

    async def _render_event(event) -> None:
        events.append(event)

    async def _clear_output() -> None:
        return None

    should_continue = await handle_line(
        bundle,
        "/fake",
        print_system=_print_system,
        render_event=_render_event,
        clear_output=_clear_output,
    )

    assert should_continue is True
    assert engine.submitted == ["continue please"]
    # The engine model is restored after the turn...
    assert engine.model == "original-model"
    # ...but the snapshot must record the model the turn ran under.
    assert backend.snapshots[-1]["model"] == "fake-submit-model"
    assert backend.snapshots[-1]["system_prompt"] == "system-prompt"


@pytest.mark.asyncio
async def test_handle_line_snapshots_current_model_without_submit_model(tmp_path: Path, monkeypatch):
    """When the command carries no submit_model the snapshot keeps the current
    model (no behavioural change)."""
    bundle, engine, backend = _build_bundle(tmp_path)

    class _NoSubmitCommand:
        async def handler(self, _args, _context):
            return CommandResult(submit_prompt="plain continuation")

    class _NoSubmitCommands:
        def lookup(self, _line):
            return (_NoSubmitCommand(), "")

    bundle.commands = _NoSubmitCommands()

    monkeypatch.setattr(
        "iterate_harness.ui.runtime.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.load_hook_registry",
        lambda _settings, _plugins: _FakeHookRegistry(),
    )
    monkeypatch.setattr("iterate_harness.ui.runtime.load_plugins", lambda *a, **k: [])
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.build_runtime_system_prompt",
        lambda *args, **kwargs: "system-prompt",
    )
    monkeypatch.setattr(
        "iterate_harness.ui.runtime.detect_provider",
        lambda _settings: SimpleNamespace(name="fake-provider", voice_supported=False, voice_reason=None),
    )
    monkeypatch.setattr("iterate_harness.ui.runtime.auth_status", lambda _settings: "ok")
    monkeypatch.setattr("iterate_harness.ui.runtime.load_keybindings", lambda: {})

    async def _print_system(_message: str) -> None:
        return None

    async def _render_event(_event) -> None:
        return None

    async def _clear_output() -> None:
        return None

    await handle_line(
        bundle,
        "/fake",
        print_system=_print_system,
        render_event=_render_event,
        clear_output=_clear_output,
    )

    assert engine.submitted == ["plain continuation"]
    assert backend.snapshots[-1]["model"] == "original-model"
