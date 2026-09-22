"""Tool for maintaining a project TODO file."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TodoWriteToolInput(BaseModel):
    """Arguments for TODO writes."""

    item: str = Field(description="TODO item text")
    checked: bool = Field(default=False)
    path: str = Field(default="TODO.md")


class TodoWriteTool(BaseTool[TodoWriteToolInput]):
    """Add or update an item in a TODO markdown file."""

    name = "todo_write"
    description = "Add a new TODO item or mark an existing one as done in a markdown checklist file."
    input_model = TodoWriteToolInput

    async def execute(self, arguments: TodoWriteToolInput, context: ToolExecutionContext) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from iterate_harness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from iterate_harness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        # Serialize read-modify-write with concurrent TODO writers and swap
        # the result in atomically (never a truncated checklist).
        from iterate_harness.utils.file_lock import exclusive_file_lock
        from iterate_harness.utils.fs import atomic_write_text, is_regular_file

        if path.exists() and not is_regular_file(path):
            return ToolResult(
                output=f"Cannot update TODO file at non-regular file ({path}) — only regular files may be written",
                is_error=True,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(path.with_name(path.name + ".lock")):
            existing = path.read_text(encoding="utf-8") if path.exists() else "# TODO\n"

            unchecked_line = f"- [ ] {arguments.item}"
            checked_line = f"- [x] {arguments.item}"
            target_line = checked_line if arguments.checked else unchecked_line

            if unchecked_line in existing and arguments.checked:
                # Mark existing unchecked item as done (in-place update)
                updated = existing.replace(unchecked_line, checked_line, 1)
            elif target_line in existing:
                # Item already in desired state — no-op
                return ToolResult(output=f"No change needed in {path}")
            else:
                # New item — append
                updated = existing.rstrip() + f"\n{target_line}\n"

            atomic_write_text(path, updated, encoding="utf-8")
        return ToolResult(output=f"Updated {path}")


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
