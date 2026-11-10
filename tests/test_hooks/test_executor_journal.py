"""Hook-executor journal recording tests (design §17.3 P15).

Every hook the executor runs — command, http, prompt or agent — must land in
the project's ``.iterate/hook-journal.jsonl`` with its event, type, matcher,
detail, outcome and duration, so the CLI and the console can answer "did my
hook fire, did it pass, and why not?". A failing journal append must never
break the caller: hook execution is inline in front of a tool call or turn
boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.api.client import ApiMessageCompleteEvent
from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.engine.messages import ConversationMessage, TextBlock
from iterate_harness.hooks import HookEvent, HookExecutionContext, HookExecutor
from iterate_harness.hooks.journal import iter_hook_executions
from iterate_harness.hooks.loader import HookRegistry
from iterate_harness.hooks.schemas import (
    CommandHookDefinition,
    HttpHookDefinition,
    PromptHookDefinition,
)


class FakeApiClient:
    """Minimal fake streaming client."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


@pytest.mark.asyncio
async def test_command_hook_records_to_journal(tmp_path: Path):
    registry = HookRegistry()
    registry.register(
        HookEvent.SESSION_START,
        CommandHookDefinition(command="printf 'booted'"),
    )
    executor = HookExecutor(
        registry,
        HookExecutionContext(cwd=tmp_path, api_client=FakeApiClient('{"ok": true}'), default_model="claude-test"),
    )

    await executor.execute(HookEvent.SESSION_START, {"event": "session_start"})

    entries = list(iter_hook_executions(tmp_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "session_start"
    assert entry["hook_type"] == "command"
    assert entry["success"] is True
    assert entry["detail"] == "printf 'booted'"
    assert entry["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_blocked_prompt_hook_records_failure(tmp_path: Path):
    registry = HookRegistry()
    registry.register(
        HookEvent.PRE_TOOL_USE,
        PromptHookDefinition(prompt="Check tool call", matcher="bash"),
    )
    executor = HookExecutor(
        registry,
        HookExecutionContext(
            cwd=tmp_path,
            api_client=FakeApiClient('{"ok": false, "reason": "blocked by policy"}'),
            default_model="claude-test",
        ),
    )

    await executor.execute(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "bash", "tool_input": {"command": "rm -rf ."}},
    )

    entries = list(iter_hook_executions(tmp_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["success"] is False
    assert entry["blocked"] is True
    assert entry["reason"] == "blocked by policy"
    assert entry["matcher"] == "bash"


@pytest.mark.asyncio
async def test_non_matching_hook_is_not_recorded(tmp_path: Path):
    registry = HookRegistry()
    registry.register(
        HookEvent.PRE_TOOL_USE,
        CommandHookDefinition(command="echo nope", matcher="nonexistent"),
    )
    executor = HookExecutor(
        registry,
        HookExecutionContext(cwd=tmp_path, api_client=FakeApiClient('{"ok": true}'), default_model="claude-test"),
    )

    await executor.execute(HookEvent.PRE_TOOL_USE, {"tool_name": "bash"})

    assert list(iter_hook_executions(tmp_path)) == []


@pytest.mark.asyncio
async def test_http_hook_records_status_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import iterate_harness.hooks.executor as executor_module

    class FakeResponse:
        is_success = False
        status_code = 500
        text = "server error"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            del url, json, headers
            return FakeResponse()

    real_client = executor_module.httpx.AsyncClient
    executor_module.httpx.AsyncClient = FakeClient  # type: ignore[assignment]
    try:
        registry = HookRegistry()
        registry.register(
            HookEvent.NOTIFICATION,
            HttpHookDefinition(url="https://example.com/hook", block_on_failure=True),
        )
        executor = HookExecutor(
            registry,
            HookExecutionContext(cwd=tmp_path, api_client=FakeApiClient('{"ok": true}'), default_model="claude-test"),
        )
        await executor.execute(HookEvent.NOTIFICATION, {"notification_type": "permission_prompt"})
    finally:
        executor_module.httpx.AsyncClient = real_client

    entries = list(iter_hook_executions(tmp_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["success"] is False
    assert entry["blocked"] is True
    assert entry["status_code"] == 500
    assert entry["hook_type"] == "http"
