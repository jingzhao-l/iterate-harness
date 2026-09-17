"""Tool for leaving plan permission mode.

Mirror of :mod:`enter_plan_mode_tool`: restores the session's permission mode
to the engine default by clearing the session-scoped override. The mode change
is carried through the tool execution context's ``metadata`` (merged back into
the engine's durable ``tool_metadata``), never written to the global settings
file.
"""

from __future__ import annotations

from pydantic import BaseModel

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

#: Session metadata key carrying the requested permission mode (mirrors the
#: engine's ``_update_plan_mode`` key under the same name).
SESSION_PERMISSION_MODE_KEY = "permission_mode"
SESSION_OVERRIDE_KEY = "session_permission_mode"
DEFAULT_MODE_VALUE = "default"


class ExitPlanModeToolInput(BaseModel):
    """No-op input model."""


class ExitPlanModeTool(BaseTool[ExitPlanModeToolInput]):
    """Switch the current session's permission mode back to default."""

    name = "exit_plan_mode"
    description = "Switch permission mode back to default for the current session."
    input_model = ExitPlanModeToolInput

    def is_read_only(self, arguments: ExitPlanModeToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: ExitPlanModeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del arguments
        if isinstance(context.metadata, dict):
            context.metadata[SESSION_PERMISSION_MODE_KEY] = DEFAULT_MODE_VALUE
            context.metadata.pop(SESSION_OVERRIDE_KEY, None)
        return ToolResult(output="Plan mode disabled. Mutating tools are permitted again.")