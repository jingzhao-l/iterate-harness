"""Tool for spawning local agent tasks."""

from __future__ import annotations

import logging
from typing import Literal, cast

from pydantic import BaseModel, Field

from iterate_harness.coordinator.agent_definitions import get_agent_definition
from iterate_harness.coordinator.coordinator_mode import get_team_registry
from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY
from iterate_harness.hooks import HookEvent
from iterate_harness.swarm.registry import get_backend_registry
from iterate_harness.swarm.types import TeammateSpawnConfig
from iterate_harness.tasks import TaskRecord, get_task_manager
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

AgentTaskType = Literal["local_agent", "remote_agent", "in_process_teammate"]


class AgentToolInput(BaseModel):
    """Arguments for local agent spawning."""

    description: str = Field(description="Short description of the delegated work")
    prompt: str = Field(description="Full prompt for the local agent")
    subagent_type: str | None = Field(
        default=None,
        description="Agent type for definition lookup (e.g. 'general-purpose', 'Explore', 'worker')",
    )
    model: str | None = Field(default=None)
    command: str | None = Field(default=None, description="Override spawn command")
    team: str | None = Field(default=None, description="Optional team to attach the agent to")
    mode: str = Field(
        default="local_agent",
        description="Agent mode: local_agent, remote_agent, or in_process_teammate",
    )


class AgentTool(BaseTool[AgentToolInput]):
    """Spawn a background agent task (subprocess or in-process teammate)."""

    name = "agent"
    description = "Spawn a local background agent task."
    input_model = AgentToolInput

    async def execute(self, arguments: AgentToolInput, context: ToolExecutionContext) -> ToolResult:
        if arguments.mode not in {"local_agent", "remote_agent", "in_process_teammate"}:
            return ToolResult(
                output="Invalid mode. Use local_agent, remote_agent, or in_process_teammate.",
                is_error=True,
            )

        # Look up agent definition if subagent_type is specified
        agent_def = None
        if arguments.subagent_type:
            agent_def = get_agent_definition(arguments.subagent_type)

        # Resolve team and agent name for the swarm backend
        team = arguments.team or "default"
        agent_name = arguments.subagent_type or "agent"

        # Pick the execution backend from the requested mode. In-process
        # teammates run as asyncio Tasks inside this process (low overhead,
        # shared memory) and are registered with the BackgroundTaskManager by
        # InProcessBackend.spawn, so the task tools keep working for them.
        # local_agent / remote_agent use the subprocess transport so they are
        # pollable via BackgroundTaskManager and available on every platform.
        registry = get_backend_registry()
        if arguments.mode == "in_process_teammate":
            try:
                executor = registry.get_executor("in_process")
            except KeyError:
                # Platform without in-process mailbox support (rare): fall back
                # to the pollable subprocess transport; the caller still sees a
                # task record regardless of transport.
                logger.warning(
                    "in_process backend unavailable; falling back to subprocess transport"
                )
                executor = registry.get_executor("subprocess")
        else:
            executor = registry.get_executor("subprocess")

        config = TeammateSpawnConfig(
            name=agent_name,
            team=team,
            prompt=arguments.prompt,
            cwd=str(context.cwd),
            parent_session_id="main",
            model=arguments.model or (agent_def.model if agent_def else None),
            command=arguments.command,
            system_prompt=agent_def.system_prompt if agent_def else None,
            permissions=agent_def.permissions if agent_def else [],
            # Forward the definition's tool posture to the spawned teammate.
            # Without this a definition's ``tools`` allow-list and
            # ``disallowed_tools`` deny-list stopped at the definition: the
            # subagent inherited the parent's full registry, so a
            # read-only "Explore" agent could still write files.
            allowed_tools=list(agent_def.tools) if agent_def and agent_def.tools else None,
            denied_tools=(
                list(agent_def.disallowed_tools)
                if agent_def and agent_def.disallowed_tools
                else None
            ),
            task_type=cast(AgentTaskType, arguments.mode),
            # Worker inherits the leader's defensive kernel (design §20.5):
            # a ``code``-mode leader spawns ``code`` workers so each subagent
            # enforces the same per-action invariant guard + atomic rollback.
            task_mode=(
                "code"
                if context.metadata.get(DEFENSIVE_KERNEL_KEY) is not None
                else None
            ),
        )

        try:
            result = await executor.spawn(config)
        except Exception as exc:
            logger.error("Failed to spawn agent: %s", exc)
            return ToolResult(output=str(exc), is_error=True)

        if not result.success:
            return ToolResult(output=result.error or "Failed to spawn agent", is_error=True)

        if arguments.team:
            team_registry = get_team_registry()
            try:
                team_registry.add_agent(arguments.team, result.task_id)
            except ValueError:
                team_registry.create_team(arguments.team)
                team_registry.add_agent(arguments.team, result.task_id)

        if context.hook_executor is not None:
            manager = get_task_manager()
            hook_executor = context.hook_executor
            unregister = None

            async def _emit_subagent_stop(task_record: TaskRecord) -> None:
                nonlocal unregister
                if task_record.id != result.task_id:
                    return
                if unregister is not None:
                    unregister()
                    unregister = None
                await hook_executor.execute(
                    HookEvent.SUBAGENT_STOP,
                    {
                        "event": HookEvent.SUBAGENT_STOP.value,
                        "agent_id": result.agent_id,
                        "task_id": result.task_id,
                        "backend_type": result.backend_type,
                        "status": task_record.status,
                        "return_code": task_record.return_code,
                        "description": arguments.description,
                        "subagent_type": arguments.subagent_type or "agent",
                        "team": team,
                        "mode": arguments.mode,
                    },
                )

            unregister = manager.register_completion_listener(_emit_subagent_stop)
            task_record = manager.get_task(result.task_id)
            if task_record is not None and task_record.status in {"completed", "failed", "killed"}:
                await _emit_subagent_stop(task_record)

        return ToolResult(
            output=(
                f"Spawned agent {result.agent_id} "
                f"(task_id={result.task_id}, backend={result.backend_type})"
            ),
            metadata={
                "agent_id": result.agent_id,
                "task_id": result.task_id,
                "backend_type": result.backend_type,
                "description": arguments.description,
            },
        )
