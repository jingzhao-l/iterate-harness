"""Engine-level wiring tests for the code-mode defensive kernel.

Verifies that ``QueryEngine`` builds a per-query ``DefensiveKernel`` in
``code`` mode (and not in ``iterate`` mode) and that ``_execute_tool_call``
snapshots mutating file tools, runs the post-check, and surfaces a rollback
as a tool error the model must respond to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.config.settings import PermissionSettings
from iterate_harness.defensive.kernel import DefensiveKernel
from iterate_harness.engine.query import QueryContext, _execute_tool_call
from iterate_harness.engine.query_engine import (
    TASK_MODE_CODE,
    TASK_MODE_ITERATE,
    QueryEngine,
)
from iterate_harness.permissions import PermissionChecker, PermissionMode
from iterate_harness.tools import create_default_tool_registry


def _permission_checker() -> PermissionChecker:
    # FULL_AUTO so mutating file tools run without an interactive approval
    # prompt; the defensive-kernel behaviour is what these tests exercise.
    return PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))


def _query_context(tmp_path: Path, *, kernel: DefensiveKernel | None) -> QueryContext:
    return QueryContext(
        api_client=None,  # type: ignore[arg-type] - not used by tool dispatch
        tool_registry=create_default_tool_registry(),
        permission_checker=_permission_checker(),
        cwd=tmp_path,
        model="test-model",
        system_prompt="",
        max_tokens=1024,
        defensive_kernel=kernel,
    )


@pytest.mark.asyncio
async def test_mutating_tool_rolls_back_when_invariant_violated(tmp_path: Path) -> None:
    script = tmp_path / "check.sh"
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    from iterate_harness.iterate.config_loader import EffectiveConfig
    from iterate_harness.iterate.types import IterateConfig, InvariantConfig

    config = IterateConfig(invariants=InvariantConfig(commands={"syntax": ["./check.sh"]}))
    effective = EffectiveConfig(config=config, source="override", override={"invariants": {"commands": {"syntax": ["./check.sh"]}}})
    kernel = DefensiveKernel(tmp_path, effective)

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")

    context = _query_context(tmp_path, kernel=kernel)
    result = await _execute_tool_call(
        context,
        "write_file",
        "use_1",
        {"path": "a.py", "content": "v2"},
    )

    assert result.is_error
    assert "invariant violated" in result.content
    # Atomic transaction: the edit was rolled back to its pre-call bytes.
    assert target.read_text(encoding="utf-8") == "v1"


@pytest.mark.asyncio
async def test_mutating_tool_commits_when_invariants_pass(tmp_path: Path) -> None:
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    from iterate_harness.iterate.config_loader import EffectiveConfig
    from iterate_harness.iterate.types import IterateConfig, InvariantConfig

    config = IterateConfig(invariants=InvariantConfig(commands={"syntax": ["./ok.sh"]}))
    effective = EffectiveConfig(config=config, source="override", override={"invariants": {"commands": {"syntax": ["./ok.sh"]}}})
    kernel = DefensiveKernel(tmp_path, effective)

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")

    context = _query_context(tmp_path, kernel=kernel)
    result = await _execute_tool_call(
        context,
        "write_file",
        "use_1",
        {"path": "a.py", "content": "v2"},
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "v2"


@pytest.mark.asyncio
async def test_mutating_tool_without_kernel_is_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")

    context = _query_context(tmp_path, kernel=None)
    result = await _execute_tool_call(
        context,
        "write_file",
        "use_1",
        {"path": "a.py", "content": "v2"},
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "v2"


@pytest.mark.asyncio
async def test_kernel_visible_to_tools_via_exec_metadata(tmp_path: Path) -> None:
    """Tools must be able to reach the kernel through their exec metadata."""
    from pydantic import BaseModel

    from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY
    from iterate_harness.tools.base import BaseTool, ToolResult

    kernel = DefensiveKernel(tmp_path)
    captured: dict[str, object] = {}

    class ProbeInput(BaseModel):
        pass

    class ProbeTool(BaseTool[ProbeInput]):
        name = "probe_kernel"
        description = "capture exec metadata for assertions"
        input_model = ProbeInput

        async def execute(self, arguments, context) -> ToolResult:
            captured["kernel"] = context.metadata.get(DEFENSIVE_KERNEL_KEY)
            return ToolResult(output="probed")

    registry = create_default_tool_registry()
    registry.register(ProbeTool())
    context = QueryContext(
        api_client=None,  # type: ignore[arg-type]
        tool_registry=registry,
        permission_checker=_permission_checker(),
        cwd=tmp_path,
        model="test-model",
        system_prompt="",
        max_tokens=1024,
        defensive_kernel=kernel,
    )

    result = await _execute_tool_call(context, "probe_kernel", "use_1", {})

    assert not result.is_error
    assert captured["kernel"] is kernel
    # The kernel is per-query state: it must never leak into the durable
    # session tool_metadata (which persists across submissions).
    assert context.tool_metadata is None or DEFENSIVE_KERNEL_KEY not in context.tool_metadata


# ---------------------------------------------------------------------------
# QueryEngine — kernel construction per task mode
# ---------------------------------------------------------------------------


def _engine(cwd: Path, *, mode: str) -> QueryEngine:
    engine = QueryEngine(
        api_client=None,  # type: ignore[arg-type]
        tool_registry=create_default_tool_registry(),
        permission_checker=_permission_checker(),
        cwd=cwd,
        model="test-model",
        system_prompt="",
    )
    engine.set_task_mode(mode)
    return engine


def test_code_mode_constructs_defensive_kernel(tmp_path: Path) -> None:
    engine = _engine(tmp_path, mode=TASK_MODE_CODE)
    kernel = engine._new_defensive_kernel()

    assert isinstance(kernel, DefensiveKernel)
    assert kernel.enabled


def test_iterate_mode_has_no_kernel(tmp_path: Path) -> None:
    engine = _engine(tmp_path, mode=TASK_MODE_ITERATE)
    kernel = engine._new_defensive_kernel()

    assert kernel is None
