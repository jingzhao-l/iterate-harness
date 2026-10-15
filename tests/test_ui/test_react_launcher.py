"""Tests for the React terminal launcher path."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from iterate_harness.ui.app import run_print_mode, run_repl, run_task_worker
from iterate_harness.engine.stream_events import AssistantTurnComplete
from iterate_harness.engine.messages import ConversationMessage, TextBlock
from iterate_harness.ui.react_launcher import build_backend_command


class _AsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_build_backend_command_includes_flags():
    command = build_backend_command(
        cwd="/tmp/demo",
        model="kimi-k2.5",
        base_url="https://api.moonshot.cn/anthropic",
        system_prompt="system",
    )
    assert command[:3] == [command[0], "-m", "iterate_harness"]
    assert "--backend-only" in command
    assert "--cwd" in command
    assert "--model" in command
    assert "--base-url" in command
    assert "--system-prompt" in command
    # Credentials must never surface in the child argv (process listings).
    assert "--api-key" not in command


def test_build_backend_command_forwards_settings_and_permission_flags():
    command = build_backend_command(
        cwd="/tmp/demo",
        model="kimi-k2.5",
        permission_mode="full_auto",
        config_path="/tmp/custom-settings.json",
        effort="high",
        verbose=True,
        allowed_tools=["bash", "read_file"],
        disallowed_tools=["write_file"],
    )
    assert command[command.index("--permission-mode") + 1] == "full_auto"
    assert command[command.index("--settings") + 1] == "/tmp/custom-settings.json"
    assert command[command.index("--effort") + 1] == "high"
    assert "--verbose" in command
    allowed_idx = command.index("--allowed-tools")
    assert command[allowed_idx : allowed_idx + 3] == ["--allowed-tools", "bash", "--allowed-tools"]
    assert command[command.index("--allowed-tools", allowed_idx + 2) + 1] == "read_file"
    denied_idx = command.index("--disallowed-tools")
    assert command[denied_idx + 1] == "write_file"


@pytest.mark.asyncio
async def test_run_repl_uses_react_launcher_by_default(monkeypatch):
    seen = {}

    async def _launch(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("iterate_harness.ui.app.launch_react_tui", _launch)
    await run_repl(prompt="hi", cwd="/tmp/demo", model="kimi-k2.5")

    assert seen["prompt"] == "hi"
    assert seen["cwd"] == "/tmp/demo"
    assert seen["model"] == "kimi-k2.5"


@pytest.mark.asyncio
async def test_run_repl_forwards_settings_and_permission_params(monkeypatch):
    seen = {}

    async def _launch(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("iterate_harness.ui.app.launch_react_tui", _launch)
    await run_repl(
        prompt="hi",
        cwd="/tmp/demo",
        permission_mode="full_auto",
        config_path="/tmp/custom-settings.json",
        effort="high",
        verbose=True,
        allowed_tools=["bash"],
        disallowed_tools=["write_file"],
    )

    assert seen["permission_mode"] == "full_auto"
    assert seen["config_path"] == "/tmp/custom-settings.json"
    assert seen["effort"] == "high"
    assert seen["verbose"] is True
    assert seen["allowed_tools"] == ["bash"]
    assert seen["disallowed_tools"] == ["write_file"]


@pytest.mark.asyncio
async def test_run_print_mode_passes_cwd_to_build_runtime(monkeypatch):
    seen = {}

    async def _build_runtime(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            app_state=SimpleNamespace(get=lambda: None),
            mcp_manager=SimpleNamespace(list_statuses=lambda: []),
            commands=SimpleNamespace(list_commands=lambda: []),
            events=_AsyncIterator(),
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(*_args, **_kwargs):
        return None

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)

    await run_print_mode(prompt="hi", cwd="/tmp/demo")

    assert seen["cwd"] == "/tmp/demo"


@pytest.mark.asyncio
async def test_run_print_mode_marks_remote_context(monkeypatch):
    """run_print_mode is the orchestrator-driven surface: it must tag the
    handle_line invocation as remote so local-only management commands are
    refused unless the caller opts in."""
    seen = {}

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(
                set=lambda **_kwargs: None, get=lambda: None
            ),
            hook_executor=SimpleNamespace(
                update_registry=lambda *_a, **_k: None
            ),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=True,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, line
        seen.update(kwargs)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)

    await run_print_mode(prompt="hello", cwd="/tmp/demo")
    assert seen.get("remote_context") is True
    assert seen.get("remote_admin_opt_in_requested") is False

    await run_print_mode(prompt="hello", cwd="/tmp/demo", admin_opt_in=True)
    assert seen.get("remote_admin_opt_in_requested") is True


@pytest.mark.asyncio
async def test_run_task_worker_reads_one_shot_json_line(monkeypatch):
    seen = []

    class _FakeStdin:
        def __init__(self):
            self._lines = iter([
                '{"text":"follow up from coordinator","from":"coordinator"}\n',
            ])

        def readline(self):
            return next(self._lines, "")

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=False,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, kwargs
        seen.append(line)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.sys.stdin", _FakeStdin())

    await run_task_worker(cwd="/tmp/demo")

    assert seen == ["follow up from coordinator"]


@pytest.mark.asyncio
async def test_run_task_worker_decodes_multiline_json_payload(monkeypatch):
    seen = []

    class _FakeStdin:
        def __init__(self):
            self._lines = iter([
                '{"text":"line 1\\nline 2\\nline 3","from":"coordinator"}\n',
            ])

        def readline(self):
            return next(self._lines, "")

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=False,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, kwargs
        seen.append(line)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.sys.stdin", _FakeStdin())

    await run_task_worker(cwd="/tmp/demo")

    assert seen == ["line 1\nline 2\nline 3"]


@pytest.mark.asyncio
async def test_run_repl_forwards_restore_and_append_to_react_launcher(monkeypatch):
    """--continue/--resume restore state must reach the spawned backend host
    via the launcher (it used to be silently dropped on the React TUI path)."""
    seen = {}

    async def _launch(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("iterate_harness.ui.app.launch_react_tui", _launch)
    await run_repl(
        prompt=None,
        cwd="/tmp/demo",
        restore_messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        restore_tool_metadata={"permission_mode": "default"},
        restore_session_id="abc123",
        append_system_prompt="be brief",
    )

    assert seen["restore_session_id"] == "abc123"
    assert seen["append_system_prompt"] == "be brief"


@pytest.mark.asyncio
async def test_run_repl_rejects_react_restore_without_session_id(monkeypatch):
    async def _launch(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError(f"launch_react_tui must not be called: {kwargs}")

    monkeypatch.setattr("iterate_harness.ui.app.launch_react_tui", _launch)

    with pytest.raises(RuntimeError, match="restore_session_id"):
        await run_repl(
            prompt=None,
            cwd="/tmp/demo",
            restore_messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        )


@pytest.mark.asyncio
async def test_run_repl_backend_only_forwards_restore_and_append(monkeypatch):
    seen = {}

    async def _run_backend_host(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("iterate_harness.ui.app.run_backend_host", _run_backend_host)
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    await run_repl(
        backend_only=True,
        cwd="/tmp/demo",
        restore_messages=messages,
        restore_tool_metadata={"permission_mode": "default"},
        append_system_prompt="be brief",
    )

    assert seen["restore_messages"] == messages
    assert seen["restore_tool_metadata"] == {"permission_mode": "default"}
    assert seen["append_system_prompt"] == "be brief"


def test_build_backend_command_includes_restore_and_append_flags():
    command = build_backend_command(
        cwd="/tmp/demo",
        system_prompt="system",
        append_system_prompt="also do X",
        restore_session_id="abc123",
    )
    append_idx = command.index("--append-system-prompt")
    assert command[append_idx + 1] == "also do X"
    restore_idx = command.index("--restore-session")
    assert command[restore_idx + 1] == "abc123"
    # Without the flags they must not appear at all.
    plain = build_backend_command(cwd="/tmp/demo")
    assert "--restore-session" not in plain
    assert "--append-system-prompt" not in plain


@pytest.mark.asyncio
async def test_run_print_mode_passes_append_system_prompt_to_build_runtime(monkeypatch):
    seen = {}

    async def _build_runtime(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            app_state=SimpleNamespace(get=lambda: None),
            mcp_manager=SimpleNamespace(list_statuses=lambda: []),
            commands=SimpleNamespace(list_commands=lambda: []),
            events=_AsyncIterator(),
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(*_args, **_kwargs):
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)

    await run_print_mode(prompt="hi", cwd="/tmp/demo", append_system_prompt="also do X")

    assert seen["append_system_prompt"] == "also do X"


@pytest.mark.asyncio
async def test_run_print_mode_permission_prompt_fails_closed(monkeypatch, capsys):
    """No TTY in print mode: the permission prompt must DENY with a clear
    message instead of auto-approving (fail open)."""
    captured = {}

    async def _build_runtime(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            app_state=SimpleNamespace(get=lambda: None),
            mcp_manager=SimpleNamespace(list_statuses=lambda: []),
            commands=SimpleNamespace(list_commands=lambda: []),
            events=_AsyncIterator(),
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(*_args, **_kwargs):
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)

    await run_print_mode(prompt="hi", cwd="/tmp/demo")

    permission_prompt = captured["permission_prompt"]
    assert await permission_prompt("bash", "runs a command") is False
    err = capsys.readouterr().err
    assert "Permission denied (non-interactive mode)" in err
    assert "bash" in err
    assert "full_auto" in err


@pytest.mark.asyncio
async def test_run_print_mode_propagates_model_failures(monkeypatch):
    """Print mode must not contain model-turn failures: they surface so the
    CLI can exit non-zero (interactive consoles keep containing them)."""
    closed = []

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            app_state=SimpleNamespace(get=lambda: None),
            mcp_manager=SimpleNamespace(list_statuses=lambda: []),
            commands=SimpleNamespace(list_commands=lambda: []),
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(*_args, **kwargs):
        assert kwargs.get("propagate_errors") is True
        raise RuntimeError("model backend exploded")

    async def _close_runtime(_bundle):
        closed.append(True)

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)

    with pytest.raises(RuntimeError, match="model backend exploded"):
        await run_print_mode(prompt="hi", cwd="/tmp/demo")

    # The runtime must still be torn down on the failure path.
    assert closed == [True]


@pytest.mark.asyncio
async def test_run_task_worker_passes_append_system_prompt(monkeypatch):
    seen = {}

    class _FakeStdin:
        def readline(self):
            return ""

    async def _build_runtime(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=False,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.sys.stdin", _FakeStdin())

    await run_task_worker(cwd="/tmp/demo", append_system_prompt="also do X")

    assert seen["append_system_prompt"] == "also do X"


@pytest.mark.asyncio
async def test_run_print_mode_waits_for_coordinator_async_agents(monkeypatch):
    class _FakeEngine:
        def __init__(self) -> None:
            self.tool_metadata = {
                "async_agent_tasks": [
                    {
                        "agent_id": "worker@default",
                        "task_id": "task_123",
                        "description": "Inspect CI",
                        "notification_sent": False,
                    }
                ]
            }
            self.messages = []
            self.total_usage = SimpleNamespace(
                input_tokens=0,
                output_tokens=0,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
            self.model = "claude-test"
            self.max_turns = 200

        def set_max_turns(self, value):
            self.max_turns = value

        def set_system_prompt(self, _value):
            return None

        async def submit_message(self, message):
            self.messages.append(ConversationMessage.from_user_text(message))
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="final synthesis")]),
                usage=None,
            )

    engine = _FakeEngine()
    saved_snapshots: list[dict] = []

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=engine,
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: SimpleNamespace(model="claude-test", max_turns=200),
            session_system_prompt=lambda latest_user_prompt=None: "coordinator",
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **kwargs: saved_snapshots.append(kwargs)),
            enforce_max_turns=True,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del kwargs
        bundle.engine.messages.append(ConversationMessage.from_user_text(line))
        return True

    async def _close_runtime(_bundle):
        return None

    class _FakeTaskManager:
        def __init__(self) -> None:
            self._calls = 0

        def get_task(self, task_id):
            self._calls += 1
            status = "running" if self._calls == 1 else "completed"
            return SimpleNamespace(id=task_id, status=status, return_code=0)

        def read_task_output(self, task_id, *, max_bytes=12000):
            del task_id, max_bytes
            return "worker result <ready>"

    fake_manager = _FakeTaskManager()

    monkeypatch.setattr("iterate_harness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("iterate_harness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("iterate_harness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("iterate_harness.ui.coordinator_drain.get_task_manager", lambda: fake_manager)
    monkeypatch.setattr("iterate_harness.ui.app.is_coordinator_mode", lambda: True)

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr("iterate_harness.ui.coordinator_drain.asyncio.sleep", _sleep)

    await run_print_mode(prompt="research this", cwd="/tmp/demo")

    assert len(engine.messages) == 2
    assert engine.messages[1].text.startswith("<task-notification>")
    assert "&lt;ready&gt;" in engine.messages[1].text
    assert "worker@default" in engine.messages[1].text
    assert saved_snapshots
