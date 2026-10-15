"""Tool for creating and entering git worktrees."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

#: Bound every git subprocess this tool spawns. ``git worktree add`` can hang
#: on an NFS-mounted repo, a stuck lock file, or a credential prompt; without
#: a deadline the tool call never returns and the whole query stalls.
_GIT_TIMEOUT_SECONDS = 60


class EnterWorktreeToolInput(BaseModel):
    """Arguments for entering a worktree."""

    branch: str = Field(description="Target branch name for the worktree")
    path: str | None = Field(default=None, description="Optional worktree path")
    create_branch: bool = Field(default=True)
    base_ref: str = Field(default="HEAD", description="Base ref when creating a new branch")


class EnterWorktreeTool(BaseTool[EnterWorktreeToolInput]):
    """Create a git worktree."""

    name = "enter_worktree"
    description = "Create a git worktree and return its path."
    input_model = EnterWorktreeToolInput

    async def execute(
        self,
        arguments: EnterWorktreeToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        try:
            top_level = await _git_output(context.cwd, "rev-parse", "--show-toplevel")
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"git rev-parse --show-toplevel timed out after {_GIT_TIMEOUT_SECONDS} seconds",
                is_error=True,
            )
        except OSError as exc:
            return ToolResult(output=f"Failed to run git: {exc}", is_error=True)
        if top_level is None:
            return ToolResult(output="enter_worktree requires a git repository", is_error=True)
        repo_root = Path(top_level)
        worktree_path = _resolve_worktree_path(repo_root, arguments.branch, arguments.path)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "worktree", "add"]
        if arguments.create_branch:
            cmd.extend(["-b", arguments.branch, str(worktree_path), arguments.base_ref])
        else:
            cmd.extend([str(worktree_path), arguments.branch])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ToolResult(output=f"Failed to run git: {exc}", is_error=True)
        try:
            stdout, stderr = await _communicate_bounded(proc, timeout=_GIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"git worktree add timed out after {_GIT_TIMEOUT_SECONDS} seconds",
                is_error=True,
            )
        output = ((stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")).strip()
        if not output:
            output = f"Created worktree {worktree_path}"
        if proc.returncode != 0:
            return ToolResult(output=output, is_error=True)
        return ToolResult(output=f"{output}\nPath: {worktree_path}")


async def _communicate_bounded(
    proc: asyncio.subprocess.Process, *, timeout: float
) -> tuple[bytes | None, bytes | None]:
    """``proc.communicate()`` with a deadline that also reaps the child.

    A timed-out or cancelled wait must kill the subprocess before bubbling up,
    otherwise the git process (and any child it spawned) keeps running after
    the tool call has already returned.
    """
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_synchronously(proc)
        raise
    except asyncio.CancelledError:
        _kill_synchronously(proc)
        raise


def _kill_synchronously(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


async def _git_output(cwd: Path, *args: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await _communicate_bounded(proc, timeout=_GIT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return None
    return ((stdout or b"").decode(errors="replace") or "").strip()


def _resolve_worktree_path(repo_root: Path, branch: str, path: str | None) -> Path:
    if path:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = repo_root / resolved
        return resolved.resolve()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "worktree"
    return (repo_root / ".iterate-harness" / "worktrees" / slug).resolve()
