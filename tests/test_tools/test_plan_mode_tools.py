"""Regression tests for session-scoped plan-mode tools (enter/exit_plan_mode).

The tools are the model-invoked channel for switching the *current session's*
permission mode (design: operation console + human intervention). They must:

- affect the live session's permission decisions through the engine's
  tool-metadata override, NOT rewrite the global settings file (leaking the
  mode into unrelated future sessions);
- never be blocked while IN plan mode (``exit_plan_mode`` must remain usable
  after ``enter_plan_mode`` ran — previously it was classified as a mutating
  tool and plan-mode would block it, making exit impossible);
- keep the built-in hard boundaries (sensitive paths / forbidden patterns /
  risk areas) intact even under a ``full_auto`` override.
"""

from __future__ import annotations

import pytest

from iterate_harness.config.settings import PermissionSettings, Settings
from iterate_harness.permissions import PermissionChecker, PermissionMode
from iterate_harness.permissions.checker import build_permission_checker
from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.enter_plan_mode_tool import EnterPlanModeTool
from iterate_harness.tools.exit_plan_mode_tool import ExitPlanModeTool


def _checker() -> PermissionChecker:
    return PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))


async def test_enter_plan_mode_records_session_override():
    """enter_plan_mode records the session override in the tool metadata.

    The metadata travels back into the engine's durable tool_metadata via the
    post-execution merge, so the NEXT tool call's permission check sees it.
    """
    tool_metadata: dict[str, object] = {}
    ctx = ToolExecutionContext(cwd=__import__("pathlib").Path("."), metadata=tool_metadata)
    result = await EnterPlanModeTool().execute(None, ctx)
    assert result.is_error is False
    # The tool wrote the requested mode on the execution metadata.
    assert ctx.metadata["session_permission_mode"] == "plan"
    assert ctx.metadata["permission_mode"] == "plan"


async def test_enter_plan_mode_does_not_rewrite_global_settings(tmp_path, monkeypatch):
    """The tool must be session-scoped: the global settings file is untouched."""
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
    settings = Settings()
    settings.permission.mode = PermissionMode.DEFAULT
    from iterate_harness.config.settings import save_settings

    save_settings(settings)

    ctx = ToolExecutionContext(cwd=tmp_path, metadata={})
    await EnterPlanModeTool().execute(None, ctx)

    from iterate_harness.config.settings import load_settings

    assert load_settings().permission.mode == PermissionMode.DEFAULT


async def test_engine_override_blocks_mutating_but_allows_reads():
    """With the override recorded, mutating tools are blocked; reads are not."""
    checker = _checker()
    from iterate_harness.engine.query import _session_permission_override

    tool_metadata = {"session_permission_mode": "plan"}
    override = _session_permission_override(tool_metadata)
    assert override == "plan"

    blocked = checker.evaluate("bash", is_read_only=False, mode_override=override)
    assert blocked.allowed is False
    assert "plan mode" in blocked.reason

    read = checker.evaluate("read_file", is_read_only=True, mode_override=override)
    assert read.allowed is True


async def test_exit_plan_mode_restores_configured_mode():
    """exit_plan_mode clears the session override so mutating tools resume."""
    tool_metadata: dict[str, object] = {}
    ctx = ToolExecutionContext(cwd=__import__("pathlib").Path("."), metadata=tool_metadata)
    await EnterPlanModeTool().execute(None, ctx)
    await ExitPlanModeTool().execute(None, ctx)

    from iterate_harness.engine.query import _session_permission_override

    assert _session_permission_override(ctx.metadata) is None
    assert _session_permission_override({"session_permission_mode": "default"}) == "default"


async def test_exit_plan_mode_tool_is_read_only():
    """exit_plan_mode must be classified read-only so plan mode never blocks it.

    (A mutating exit tool would be blocked by the very plan state it should
    clear, trapping the user in plan mode through the model channel.)
    """
    tool = ExitPlanModeTool()
    assert tool.is_read_only(None) is True


async def test_plan_override_never_bypasses_sensitive_paths():
    """A full_auto override must not widen the sensitive-path hard boundary."""
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate(
        "read_file",
        is_read_only=True,
        file_path="/home/user/.ssh/id_rsa",
        mode_override="full_auto",
    )
    assert decision.allowed is False
    assert ".ssh" in decision.reason


async def test_session_override_survives_engine_metadata_merge():
    """Simulate the engine merge-back: keys written by the tool land in the
    durable tool_metadata consumed by later permission checks."""
    tool_metadata: dict[str, object] = {
        "read_file_state": [],
        "permission_mode": "default",
    }
    from copy import deepcopy

    prior = deepcopy(tool_metadata)
    exec_metadata = dict(prior)
    ctx = ToolExecutionContext(cwd=__import__("pathlib").Path("."), metadata=exec_metadata)
    await EnterPlanModeTool().execute(None, ctx)
    # Engine merge-back: keys the tool introduced or replaced are copied back.
    for key, value in exec_metadata.items():
        prior_ref = prior.get(key, object())
        if prior_ref is not value:
            tool_metadata[key] = value

    from iterate_harness.engine.query import _session_permission_override

    assert _session_permission_override(tool_metadata) == "plan"


def test_malformed_override_degrades_to_configured_mode():
    checker = _checker()
    decision = checker.evaluate(
        "bash", is_read_only=False, mode_override="not-a-mode"
    )
    # fail-closed: falls back to the configured full_auto behavior
    assert decision.allowed is True


def test_build_permission_checker_accepts_override_path():
    from iterate_harness.config.settings import IterateSettings

    settings = Settings.model_construct(
        permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
        iterate=IterateSettings(),
    )
    checker = build_permission_checker(settings)
    decision = checker.evaluate("bash", is_read_only=False, mode_override="plan")
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_engine_execution_path_enforces_plan_mode(tmp_path):
    """Full integration: run enter_plan_mode through the real ``_execute_tool_call``
    path, then verify the NEXT mutating tool call is blocked by the recorded
    session override (and reads still pass)."""
    from iterate_harness.engine.query import QueryContext, _execute_tool_call
    from iterate_harness.tools.base import ToolRegistry
    from iterate_harness.tools.file_read_tool import FileReadTool
    from iterate_harness.tools.file_write_tool import FileWriteTool

    tool_metadata: dict[str, object] = {"read_file_state": [], "permission_mode": "full_auto"}
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    registry = ToolRegistry()
    registry.register(EnterPlanModeTool())
    registry.register(FileWriteTool())
    registry.register(FileReadTool())
    ctx = QueryContext(
        api_client=None,
        tool_registry=registry,
        permission_checker=checker,
        cwd=tmp_path,
        model="test",
        system_prompt="",
        max_tokens=1,
        tool_metadata=tool_metadata,
    )

    entered = await _execute_tool_call(ctx, "enter_plan_mode", "id1", {})
    assert entered.is_error is False
    # The engine merged the tool's session override into durable tool metadata.
    assert tool_metadata.get("session_permission_mode") == "plan"

    blocked = await _execute_tool_call(
        ctx, "write_file", "id2", {"path": "notes.txt", "content": "x"}
    )
    assert blocked.is_error is True
    assert "plan mode" in blocked.content

    read = await _execute_tool_call(ctx, "read_file", "id3", {"path": "notes.txt"})
    assert read.is_error is True  # file does not exist — but NOT blocked for plan mode
    assert "plan mode" not in read.content