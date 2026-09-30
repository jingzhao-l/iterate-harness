"""Tool for listing tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from iterate_harness.tasks.manager import get_task_manager
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


#: Rows returned by default. The manager retains up to 200 terminal records,
#: so an unbounded listing dumped hundreds of lines into the model's context on
#: every swarm turn — the per-turn cost grew with session age.
DEFAULT_TASK_LIST_LIMIT = 25

#: Hard ceiling for the ``limit`` argument.
MAX_TASK_LIST_LIMIT = 200


class TaskListToolInput(BaseModel):
    """Arguments for task listing."""

    status: str | None = Field(default=None, description="Optional status filter")
    limit: int = Field(
        default=DEFAULT_TASK_LIST_LIMIT,
        ge=1,
        le=MAX_TASK_LIST_LIMIT,
        description="Maximum number of tasks to return (newest first)",
    )


class TaskListTool(BaseTool[TaskListToolInput]):
    """List background tasks."""

    name = "task_list"
    description = "List background tasks."
    input_model = TaskListToolInput

    def is_read_only(self, arguments: TaskListToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskListToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        all_tasks = get_task_manager().list_tasks(status=arguments.status)  # type: ignore[arg-type]
        if not all_tasks:
            return ToolResult(output="(no tasks)")
        shown = all_tasks[: arguments.limit]
        omitted = len(all_tasks) - len(shown)
        lines = [f"{task.id} {task.type} {task.status} {task.description}" for task in shown]
        if omitted:
            # Say what was dropped and how to reach it, instead of quietly
            # truncating a list the model has no other way to page.
            lines.append(
                f"... {omitted} more task(s) not shown (newest first). "
                f"Call task_list with limit="
                f"{min(arguments.limit * 4, MAX_TASK_LIST_LIMIT)} or a status filter."
            )
        return ToolResult(output="\n".join(lines))
