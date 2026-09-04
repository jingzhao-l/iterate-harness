"""Tool for removing git worktrees."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ExitWorktreeToolInput(BaseModel):
    """Arguments for worktree removal."""

    path: str = Field(description="Worktree path to remove")


class ExitWorktreeTool(BaseTool[ExitWorktreeToolInput]):
    """Remove a git worktree."""

    name = "exit_worktree"
    description = "Remove a git worktree by path."
    input_model = ExitWorktreeToolInput

    async def execute(
        self,
        arguments: ExitWorktreeToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = Path(arguments.path).expanduser()
        if not path.is_absolute():
            path = (context.cwd / path).resolve()
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", str(path),
            cwd=str(context.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = ((stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")).strip()
        if not output:
            output = f"Removed worktree {path}"
        return ToolResult(output=output, is_error=proc.returncode != 0)
