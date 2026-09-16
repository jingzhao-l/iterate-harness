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
        resolved = path.resolve()

        # Never operate on the main working tree: git rejects it anyway, but a
        # friendly early error beats a confusing subprocess message.
        if resolved == context.cwd.resolve():
            return ToolResult(
                output=f"Cannot remove the main working tree: {resolved}",
                is_error=True,
            )

        # Path validation: only a registered git worktree may be removed. A
        # bare argument like ``~`` / ``/`` / ``../../`` would otherwise be fed
        # straight into ``git worktree remove --force``.
        registration, registered = await _find_worktree(context.cwd, resolved)
        if not registered:
            return ToolResult(
                output=(
                    f"{resolved} is not a registered git worktree of this "
                    "repository"
                ),
                is_error=True,
            )

        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", str(resolved),
            cwd=str(registration),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = ((stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")).strip()
        if not output:
            output = f"Removed worktree {resolved}"
        return ToolResult(output=output, is_error=proc.returncode != 0)


async def _find_worktree(cwd: Path, target: Path) -> tuple[Path, bool]:
    """Return ``(git_dir, True)`` when ``target`` is a registered worktree.

    Runs ``git worktree list --porcelain`` from ``cwd`` and matches the
    resolved absolute path. Returns ``("", False)`` when the target is not a
    registered worktree of the enclosing repository.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "list", "--porcelain",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except OSError:
        return Path(""), False
    if proc.returncode != 0:
        return Path(""), False
    worktree: Path | None = None
    for line in stdout.decode(errors="replace").splitlines():
        if not line:
            worktree = None
            continue
        if line.startswith("worktree "):
            worktree = Path(line[len("worktree ") :].strip()).resolve()
        if worktree is not None and worktree == target:
            return worktree, True
    return Path(""), False
