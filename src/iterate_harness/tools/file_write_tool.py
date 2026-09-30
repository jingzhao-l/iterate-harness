"""File writing tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class FileWriteToolInput(BaseModel):
    """Arguments for the file write tool."""

    path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)


class FileWriteTool(BaseTool[FileWriteToolInput]):
    """Write complete file contents."""

    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    input_model = FileWriteToolInput

    async def execute(
        self,
        arguments: FileWriteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from iterate_harness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from iterate_harness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        # ``create_directories`` is honoured end to end: with it off, a write to
        # a missing directory fails loudly instead of the atomic writer
        # silently creating the tree the caller asked not to create.
        if not arguments.create_directories and not path.parent.is_dir():
            return ToolResult(
                output=(
                    f"Parent directory does not exist: {path.parent} "
                    "(pass create_directories=true to create it)"
                ),
                is_error=True,
            )
        # Atomic temp-file + os.replace write: a crash mid-write can never leave
        # a truncated file, and concurrent write/edit tools serialize on the
        # lock so read-modify-write races can't corrupt shared files.
        from iterate_harness.utils.file_lock import exclusive_file_lock, sidecar_lock_path
        from iterate_harness.utils.fs import atomic_write_text

        with exclusive_file_lock(sidecar_lock_path(path)):
            atomic_write_text(
                path,
                arguments.content,
                encoding="utf-8",
                create_parents=arguments.create_directories,
            )
        return ToolResult(output=f"Wrote {path}")


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
