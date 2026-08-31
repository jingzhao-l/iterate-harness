"""Tool for leaving plan permission mode."""

from __future__ import annotations

from pydantic import BaseModel

from iterate_harness.config.settings import load_settings, save_settings
from iterate_harness.permissions import PermissionMode
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ExitPlanModeToolInput(BaseModel):
    """No-op input model."""


class ExitPlanModeTool(BaseTool[ExitPlanModeToolInput]):
    """Switch settings permission mode back to default."""

    name = "exit_plan_mode"
    description = "Switch permission mode back to default."
    input_model = ExitPlanModeToolInput

    async def execute(self, arguments: ExitPlanModeToolInput, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        settings = load_settings()
        settings.permission.mode = PermissionMode.DEFAULT
        save_settings(settings)
        return ToolResult(output="Permission mode set to default")
