"""Built-in tool registration."""

from iterate_harness.mcp.client import McpClientManager
from iterate_harness.tools.agent_tool import AgentTool
from iterate_harness.tools.ask_user_question_tool import AskUserQuestionTool
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from iterate_harness.tools.bash_tool import BashTool
from iterate_harness.tools.brief_tool import BriefTool
from iterate_harness.tools.config_tool import ConfigTool
from iterate_harness.tools.cron_create_tool import CronCreateTool
from iterate_harness.tools.cron_delete_tool import CronDeleteTool
from iterate_harness.tools.cron_list_tool import CronListTool
from iterate_harness.tools.cron_toggle_tool import CronToggleTool
from iterate_harness.tools.enter_plan_mode_tool import EnterPlanModeTool
from iterate_harness.tools.enter_worktree_tool import EnterWorktreeTool
from iterate_harness.tools.exit_plan_mode_tool import ExitPlanModeTool
from iterate_harness.tools.exit_worktree_tool import ExitWorktreeTool
from iterate_harness.tools.file_edit_tool import FileEditTool
from iterate_harness.tools.file_read_tool import FileReadTool
from iterate_harness.tools.file_write_tool import FileWriteTool
from iterate_harness.tools.glob_tool import GlobTool
from iterate_harness.tools.grep_tool import GrepTool
from iterate_harness.tools.image_to_text_tool import ImageToTextTool
from iterate_harness.tools.iterate_tools import (
    IterateConfigTool,
    IterateContextTool,
    IterateDecisionLogTool,
    IterateReviewTool,
    IterateTriageTool,
    IterateValidateTool,
)
from iterate_harness.tools.list_mcp_resources_tool import ListMcpResourcesTool
from iterate_harness.tools.lsp_tool import LspTool
from iterate_harness.tools.mcp_auth_tool import McpAuthTool
from iterate_harness.tools.mcp_tool import McpToolAdapter
from iterate_harness.tools.notebook_edit_tool import NotebookEditTool
from iterate_harness.tools.read_mcp_resource_tool import ReadMcpResourceTool
from iterate_harness.tools.remote_trigger_tool import RemoteTriggerTool
from iterate_harness.tools.send_message_tool import SendMessageTool
from iterate_harness.tools.skill_tool import SkillTool
from iterate_harness.tools.sleep_tool import SleepTool
from iterate_harness.tools.task_create_tool import TaskCreateTool
from iterate_harness.tools.task_get_tool import TaskGetTool
from iterate_harness.tools.task_list_tool import TaskListTool
from iterate_harness.tools.task_output_tool import TaskOutputTool
from iterate_harness.tools.task_stop_tool import TaskStopTool
from iterate_harness.tools.task_update_tool import TaskUpdateTool
from iterate_harness.tools.team_create_tool import TeamCreateTool
from iterate_harness.tools.team_delete_tool import TeamDeleteTool
from iterate_harness.tools.todo_write_tool import TodoWriteTool
from iterate_harness.tools.tool_search_tool import ToolSearchTool
from iterate_harness.tools.web_fetch_tool import WebFetchTool
from iterate_harness.tools.web_search_tool import WebSearchTool


def create_default_tool_registry(mcp_manager: McpClientManager | None = None) -> ToolRegistry:
    """Return the default built-in tool registry."""
    registry = ToolRegistry()
    for tool in (
        BashTool(),
        AskUserQuestionTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        NotebookEditTool(),
        LspTool(),
        McpAuthTool(),
        GlobTool(),
        GrepTool(),
        ImageToTextTool(),
        SkillTool(),
        ToolSearchTool(),
        WebFetchTool(),
        WebSearchTool(),
        ConfigTool(),
        BriefTool(),
        SleepTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        CronCreateTool(),
        CronListTool(),
        CronDeleteTool(),
        CronToggleTool(),
        RemoteTriggerTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskListTool(),
        TaskStopTool(),
        TaskOutputTool(),
        TaskUpdateTool(),
        AgentTool(),
        SendMessageTool(),
        TeamCreateTool(),
        TeamDeleteTool(),
        IterateConfigTool(),
        IterateValidateTool(),
        IterateReviewTool(),
        IterateDecisionLogTool(),
        IterateContextTool(),
        IterateTriageTool(),
    ):
        registry.register(tool)
    if mcp_manager is not None:
        registry.register(ListMcpResourcesTool(mcp_manager))
        registry.register(ReadMcpResourceTool(mcp_manager))
        for tool_info in mcp_manager.list_tools():
            registry.register(McpToolAdapter(mcp_manager, tool_info))
    return registry


__all__ = [
    "BaseTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
