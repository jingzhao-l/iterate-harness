"""Regression tests for hook-executor / loader / hot-reload hardening.

Covers the review fixes:

- ``HookExecutor.execute`` contains per-hook exceptions and spawn failures
  (OSError) as failed ``HookResult``s — a broken hook never crashes the
  caller mid-tool-execution.
- Command/http hook output is bounded (8 KiB) with a visible marker.
- The notification matcher can match ``notification_type``
  (e.g. ``permission_prompt``) instead of the literal event name.
- ``load_hook_registry`` logs a warning for unknown event names instead of
  silently dropping the section.
- ``HookReloader`` keeps the previous registry when a reload fails and only
  records the mtime after a successful load.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from iterate_harness.config.settings import Settings
from iterate_harness.hooks import HookEvent
from iterate_harness.hooks.executor import (
    HookExecutionContext,
    HookExecutor,
    _bounded_hook_output,
    _matches_hook,
)
from iterate_harness.hooks.hot_reload import HookReloader
from iterate_harness.hooks.loader import HookRegistry, load_hook_registry
from iterate_harness.hooks.schemas import CommandHookDefinition


class _FakeApiClient:
    async def stream_message(self, request):  # pragma: no cover - unused
        del request
        return
        yield


class _FakeProcess:
    """Minimal stand-in for the subprocess returned by create_shell_subprocess."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        return None


def _executor(tmp_path: Path, hook: CommandHookDefinition) -> HookExecutor:
    from iterate_harness.hooks.loader import HookRegistry

    registry = HookRegistry()
    registry.register(HookEvent.SESSION_START, hook)
    return HookExecutor(
        registry,
        HookExecutionContext(cwd=tmp_path, api_client=_FakeApiClient(), default_model="claude-test"),
    )


@pytest.mark.asyncio
async def test_spawn_oserror_becomes_failed_result_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _executor(tmp_path, CommandHookDefinition(command="definitely-not-a-real-binary"))

    async def _boom(*args: object, **kwargs: object) -> _FakeProcess:
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr("iterate_harness.hooks.executor.create_shell_subprocess", _boom)

    result = await executor.execute(HookEvent.SESSION_START, {"event": "session_start"})

    assert len(result.results) == 1
    assert result.results[0].success is False
    assert "failed to spawn" in result.results[0].reason


@pytest.mark.asyncio
async def test_hook_raising_exception_is_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _executor(tmp_path, CommandHookDefinition(command="whatever"))

    async def _boom(*args: object, **kwargs: object) -> _FakeProcess:
        raise RuntimeError("hook exploded")

    monkeypatch.setattr("iterate_harness.hooks.executor.create_shell_subprocess", _boom)

    result = await executor.execute(HookEvent.SESSION_START, {"event": "session_start"})

    assert result.blocked is False
    assert result.results[0].success is False
    assert "RuntimeError" in result.results[0].reason


@pytest.mark.asyncio
async def test_command_hook_output_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = _executor(tmp_path, CommandHookDefinition(command="yes"))
    huge = "x" * 50_000

    async def _fake_spawn(*args: object, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(stdout=huge.encode())

    monkeypatch.setattr("iterate_harness.hooks.executor.create_shell_subprocess", _fake_spawn)

    result = await executor.execute(HookEvent.SESSION_START, {"event": "session_start"})

    output = result.results[0].output
    assert len(output) < len(huge)
    assert "[hook output truncated" in output


def test_bounded_hook_output_short_text_untouched() -> None:
    assert _bounded_hook_output("small") == "small"


def test_notification_matcher_matches_notification_type() -> None:
    hook = CommandHookDefinition(command="true", matcher="permission_prompt")
    # The exact payload shape the engine sends for a permission prompt
    # (engine/query.py): it carries ``tool_name``, which must not shadow the
    # ``notification_type`` the matcher is keyed on.
    payload = {
        "event": "notification",
        "notification_type": "permission_prompt",
        "tool_name": "bash",
        "reason": "requires confirmation",
    }

    assert _matches_hook(hook, payload) is True

    other = {"event": "notification", "notification_type": "iterate_pause"}
    assert _matches_hook(hook, other) is False

    # A tool matcher still matches on tool_name for non-notification events.
    tool_hook = CommandHookDefinition(command="true", matcher="bash")
    assert _matches_hook(tool_hook, {"event": "pre_tool_use", "tool_name": "bash"}) is True
    assert _matches_hook(tool_hook, {"event": "pre_tool_use", "tool_name": "glob"}) is False


def test_loader_warns_on_unknown_event_and_drops_it(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        hooks={"session_start_typo": [CommandHookDefinition(command="true")]}
    )

    with caplog.at_level(logging.WARNING):
        registry = load_hook_registry(settings)

    assert registry.get(HookEvent.SESSION_START) == []
    assert any(
        "session_start_typo" in record.getMessage() for record in caplog.records
    ), "the invalid event name must be logged so the user can fix the typo"


def test_hot_reload_keeps_previous_registry_when_reload_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        Settings(hooks={"session_start": [CommandHookDefinition(command="true")]}).model_dump_json(
            exclude_none=True
        ),
        encoding="utf-8",
    )

    reloader = HookReloader(settings_path)
    first = reloader.current_registry()
    assert len(first.get(HookEvent.SESSION_START)) == 1

    def _fail(*args: object, **kwargs: object) -> HookRegistry:
        raise ValueError("simulated broken settings")

    monkeypatch.setattr("iterate_harness.hooks.hot_reload.load_hook_registry", _fail)
    os.utime(settings_path, ns=(1, 1))  # force an mtime change
    kept = reloader.current_registry()
    assert len(kept.get(HookEvent.SESSION_START)) == 1, "previous registry must survive a failed reload"

    # Once loading works again and the mtime changes, the reload recovers.
    monkeypatch.undo()
    os.utime(settings_path, ns=(2, 2))
    recovered = reloader.current_registry()
    assert len(recovered.get(HookEvent.SESSION_START)) == 1
