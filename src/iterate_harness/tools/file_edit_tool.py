"""String-based file editing tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class FileEditToolInput(BaseModel):
    """Arguments for the file edit tool."""

    path: str = Field(description="Path of the file to edit")
    # ``old_str`` must never be empty: ``str.replace("", x)`` inserts the
    # replacement between *every* character and turns a one-line edit into
    # silent, total file destruction that still reports success. Reject it at
    # the schema boundary so the model gets a validation error instead.
    old_str: str = Field(min_length=1, description="Existing text to replace")
    new_str: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False)


class FileEditTool(BaseTool[FileEditToolInput]):
    """Replace text in an existing file."""

    name = "edit_file"
    description = "Edit an existing file by replacing a string."
    input_model = FileEditToolInput

    async def execute(
        self,
        arguments: FileEditToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from iterate_harness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from iterate_harness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        # Serialize with other write/edit tools on the same file and swap the
        # update in atomically so a crash mid-write can't truncate the file.
        from iterate_harness.utils.file_lock import exclusive_file_lock, sidecar_lock_path
        from iterate_harness.utils.fs import atomic_write_text, is_regular_file

        if not is_regular_file(path):
            return ToolResult(
                output=f"Cannot edit non-regular file ({path}) — only regular files may be edited",
                is_error=True,
            )

        if not arguments.old_str:
            # Belt-and-braces: a caller constructing the model directly (bypassing
            # pydantic validation) must not be able to shred the file either.
            return ToolResult(
                output="old_str must not be empty — it is the text to look for, not an insertion point",
                is_error=True,
            )

        with exclusive_file_lock(sidecar_lock_path(path)):
            try:
                original = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ToolResult(
                    output=(
                        f"Cannot edit {path}: the file is not valid UTF-8. "
                        "Convert it to UTF-8 before editing."
                    ),
                    is_error=True,
                )
            if arguments.old_str not in original:
                return ToolResult(output="old_str was not found in the file", is_error=True)

            if arguments.replace_all:
                updated = original.replace(arguments.old_str, arguments.new_str)
            else:
                occurrences = original.count(arguments.old_str)
                if occurrences > 1:
                    # Editing the first of several identical matches silently
                    # edits the wrong site; tell the model which lines are
                    # candidates so it can widen ``old_str`` or pass
                    # ``replace_all``.
                    lines = [
                        index + 1
                        for index, line in enumerate(original.splitlines())
                        if arguments.old_str in line
                    ]
                    return ToolResult(
                        output=(
                            f"old_str is not unique in {path}: found {occurrences} matches "
                            f"(on line(s) {', '.join(str(n) for n in lines)}). "
                            "Pass a longer old_str that includes surrounding context, "
                            "or set replace_all=true."
                        ),
                        is_error=True,
                    )
                updated = original.replace(arguments.old_str, arguments.new_str, 1)

            atomic_write_text(path, updated, encoding="utf-8")
        return ToolResult(output=f"Updated {path}")


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
