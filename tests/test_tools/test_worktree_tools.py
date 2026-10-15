"""Regression tests: worktree tools must bound their git subprocesses."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools import enter_worktree_tool
from iterate_harness.tools import exit_worktree_tool
from iterate_harness.tools.enter_worktree_tool import EnterWorktreeTool, EnterWorktreeToolInput
from iterate_harness.tools.exit_worktree_tool import ExitWorktreeTool, ExitWorktreeToolInput


class _StuckProcess:
    """Subprocess double whose ``communicate`` never finishes on its own."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(60)
        return b"", b""

    async def wait(self):
        if self.returncode is None:
            await asyncio.sleep(60)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_enter_worktree_times_out_instead_of_hanging(monkeypatch, tmp_path: Path):
    proc = _StuckProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return proc

    monkeypatch.setattr(enter_worktree_tool.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(enter_worktree_tool, "_GIT_TIMEOUT_SECONDS", 0.05)

    result = await EnterWorktreeTool().execute(
        EnterWorktreeToolInput(branch="feature/demo"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "timed out" in result.output
    # The stuck git process must be killed, not leaked behind the tool call.
    assert proc.killed is True
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_exit_worktree_times_out_instead_of_hanging(monkeypatch, tmp_path: Path):
    proc = _StuckProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return proc

    monkeypatch.setattr(exit_worktree_tool.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(exit_worktree_tool, "_GIT_TIMEOUT_SECONDS", 0.05)

    result = await ExitWorktreeTool().execute(
        ExitWorktreeToolInput(path=str(tmp_path / "some-worktree")),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "timed out" in result.output
    assert proc.killed is True
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_enter_worktree_reports_missing_git_as_error_not_crash(monkeypatch, tmp_path: Path):
    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError("git")

    monkeypatch.setattr(enter_worktree_tool.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await EnterWorktreeTool().execute(
        EnterWorktreeToolInput(branch="feature/demo"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "Failed to run git" in result.output
