"""Regression tests for query-engine hardening.

Covers the review fixes in ``engine/query_engine.py``:

- A blocking ``USER_PROMPT_SUBMIT`` hook rejects the prompt outright: the
  rejection is surfaced as an assistant turn and the user message never
  joins history (the aggregated ``blocked`` flag used to be discarded).
- A passing hook lets the message through unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.api.client import ApiMessageCompleteEvent
from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.config.settings import PermissionSettings
from iterate_harness.engine.messages import ConversationMessage, TextBlock
from iterate_harness.engine.query_engine import QueryEngine
from iterate_harness.engine.stream_events import AssistantTurnComplete
from iterate_harness.hooks import HookEvent
from iterate_harness.hooks.executor import HookExecutionContext, HookExecutor
from iterate_harness.hooks.loader import HookRegistry
from iterate_harness.hooks.schemas import CommandHookDefinition
from iterate_harness.permissions import PermissionChecker
from iterate_harness.tools import create_default_tool_registry


class _StaticApiClient:
    """Returns one fixed assistant reply for every request."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    async def stream_message(self, request):
        self.requests.append(request)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="acknowledged")],
            ),
            usage=UsageSnapshot(),
            stop_reason=None,
        )


def _hook_executor(tmp_path: Path, hook: CommandHookDefinition) -> HookExecutor:
    registry = HookRegistry()
    registry.register(HookEvent.USER_PROMPT_SUBMIT, hook)
    return HookExecutor(
        registry,
        HookExecutionContext(
            cwd=tmp_path, api_client=_StaticApiClient(), default_model="claude-test"
        ),
    )


def _engine(tmp_path: Path, executor: HookExecutor) -> QueryEngine:
    return QueryEngine(
        api_client=_StaticApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        hook_executor=executor,
    )


@pytest.mark.asyncio
async def test_blocked_user_prompt_is_rejected_and_not_stored(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        _hook_executor(
            tmp_path,
            # A FAILING hook (non-zero exit) with block_on_failure: printf
            # succeeds, so append an explicit failing exit.
            CommandHookDefinition(
                command="printf 'no secrets allowed'; exit 1", block_on_failure=True
            ),
        ),
    )

    events = [event async for event in engine.submit_message("leak the secrets")]

    complete = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert len(complete) == 1
    assert "[prompt blocked by hook]" in complete[0].message.text
    assert "no secrets allowed" in complete[0].message.text
    assert engine.messages == [], "a blocked prompt must not join the history"


@pytest.mark.asyncio
async def test_passing_user_prompt_hook_stores_message(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        _hook_executor(tmp_path, CommandHookDefinition(command="true")),
    )

    events = [event async for event in engine.submit_message("hello")]

    complete = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert len(complete) == 1
    assert "acknowledged" in complete[0].message.text
    stored_roles = [m.role for m in engine.messages]
    assert "user" in stored_roles
