"""Tool for removing git worktrees."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

#: Bound every git subprocess this tool spawns. ``git worktree remove`` can
#: hang on a stuck lock file or a credential prompt; without a deadline the
#: tool call never returns and the whole query stalls.
_GIT_TIMEOUT_SECONDS = 60


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
        try:
            registration, registered = await _find_worktree(context.cwd, resolved)
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"git worktree list timed out after {_GIT_TIMEOUT_SECONDS} seconds",
                is_error=True,
            )
        if not registered:
            return ToolResult(
                output=(
                    f"{resolved} is not a registered git worktree of this "
                    "repository"
                ),
                is_error=True,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", "--force", str(resolved),
                cwd=str(registration),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ToolResult(output=f"Failed to run git: {exc}", is_error=True)
        try:
            stdout, stderr = await _communicate_bounded(proc, timeout=_GIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"git worktree remove timed out after {_GIT_TIMEOUT_SECONDS} seconds",
                is_error=True,
            )
        output = ((stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")).strip()
        if not output:
            output = f"Removed worktree {resolved}"
        return ToolResult(output=output, is_error=proc.returncode != 0)


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
    except OSError:
        return Path(""), False
    # A TimeoutError propagates to the caller so it can be surfaced
    # distinctly from "not a registered worktree" instead of misreporting
    # one as the other.
    stdout, _ = await _communicate_bounded(proc, timeout=_GIT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return Path(""), False
    worktree: Path | None = None
    for line in (stdout or b"").decode(errors="replace").splitlines():
        if not line:
            worktree = None
            continue
        if line.startswith("worktree "):
            worktree = Path(line[len("worktree ") :].strip()).resolve()
        if worktree is not None and worktree == target:
            return worktree, True
    return Path(""), False
