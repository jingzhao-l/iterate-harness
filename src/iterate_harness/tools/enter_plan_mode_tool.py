"""Tool for entering plan permission mode.

Plan mode is a *session-scoped* permission state: the model asks the engine to
stop mutating the workspace while it reasons. The mode travels through the tool
execution context's ``metadata`` (the engine merges it back into the durable
``tool_metadata``), so the change is visible to the permission layer for the
rest of the session — unlike writing the global settings file, which would leak
the mode into unrelated future sessions.
"""

from __future__ import annotations

from pydantic import BaseModel

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

#: Session metadata key carrying the requested permission mode (mirrors the
#: engine's ``_update_plan_mode`` key under the same name).
SESSION_PERMISSION_MODE_KEY = "permission_mode"
PLAN_MODE_VALUE = "plan"


class EnterPlanModeToolInput(BaseModel):
    """No-op input model."""


class EnterPlanModeTool(BaseTool[EnterPlanModeToolInput]):
    """Switch the current session's permission mode to plan."""

    name = "enter_plan_mode"
    description = "Switch permission mode to plan for the current session."
    input_model = EnterPlanModeToolInput

    def is_read_only(self, arguments: EnterPlanModeToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: EnterPlanModeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del arguments
        # Record the mode on the session metadata the engine carries. The
        # engine's post-execution merge pushes it into the durable
        # tool_metadata, so the permission layer sees it on every later call.
        if isinstance(context.metadata, dict):
            context.metadata[SESSION_PERMISSION_MODE_KEY] = PLAN_MODE_VALUE
            context.metadata["session_permission_mode"] = PLAN_MODE_VALUE
        return ToolResult(output="Plan mode enabled for this session. No files will be modified until you exit plan mode.")