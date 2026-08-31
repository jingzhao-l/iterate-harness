"""High-level conversation engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from iterate_harness.api.client import SupportsStreamingMessages
from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.coordinator.coordinator_mode import get_coordinator_user_context
from iterate_harness.engine.cost_tracker import CostTracker
from iterate_harness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock
from iterate_harness.engine.query import (
    AskUserPrompt,
    AskUserSelect,
    PermissionPrompt,
    QueryContext,
    remember_user_goal,
    run_query,
)
from iterate_harness.engine.stream_events import AssistantTurnComplete, StreamEvent
from iterate_harness.hooks import HookEvent, HookExecutor
from iterate_harness.iterate.loop_policy import IterateLoopPolicy
from iterate_harness.permissions.checker import PermissionChecker
from iterate_harness.tools.base import ToolRegistry


def _default_iterate_policy(cwd: Path) -> IterateLoopPolicy | None:
    """Build the default iterate loop policy from kernel + project settings.

    Returns ``None`` (upstream behavior preserved) when iterate is disabled
    in settings or settings cannot be read (e.g. sandboxed environments).
    """
    try:
        from iterate_harness.config.settings import load_settings
        from iterate_harness.iterate.loop_policy import IterateLoopPolicy
        from iterate_harness.iterate.settings import effective_review_rounds, project_config

        kernel = load_settings().iterate
        if not kernel.enabled:
            return None
        project = project_config(cwd)
        rounds = effective_review_rounds(kernel, project)
        return IterateLoopPolicy(
            max_review_rounds=rounds,
            require_fix_approval=kernel.require_fix_approval,
            total_token_budget=project.config.token_budget,
            budget_usd=project.config.budget_usd,
            max_turns_per_minute=project.config.max_turns_per_minute,
            stall_pause_rounds=kernel.stall_pause_rounds,
            budget_pause_min_rounds=kernel.budget_pause_min_rounds,
            worktree_isolation=(
                kernel.worktree_isolation or project.config.worktree_isolation
            ),
            price_overrides={
                model: (price[0], price[1])
                for model, price in (kernel.price_overrides or {}).items()
            },
        )
    except Exception:  # noqa: BLE001 - policy wiring must never break the engine
        return None


def _default_reasoning_effort(cwd: Path) -> str | None:
    """Read the project's configured ``reasoning_effort`` (None = provider default).

    Mirrors the defensive wiring of :func:`_default_iterate_policy`: settings
    are read best-effort so a broken/unreadable config never breaks the engine.
    """
    try:
        from iterate_harness.iterate.settings import project_config

        return project_config(cwd).config.reasoning_effort
    except Exception:  # noqa: BLE001 - config wiring must never break the engine
        return None


class QueryEngine:
    """Owns conversation history and the tool-aware model loop."""

    def __init__(
        self,
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        permission_checker: PermissionChecker,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        max_tokens: int = 4096,
        context_window_tokens: int | None = None,
        auto_compact_threshold_tokens: int | None = None,
        max_turns: int | None = 8,
        permission_prompt: PermissionPrompt | None = None,
        ask_user_prompt: AskUserPrompt | None = None,
        ask_user_select: AskUserSelect | None = None,
        hook_executor: HookExecutor | None = None,
        tool_metadata: dict[str, object] | None = None,
        iterate_policy: object | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._api_client = api_client
        self._tool_registry = tool_registry
        self._permission_checker = permission_checker
        self._cwd = Path(cwd).resolve()
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._context_window_tokens = context_window_tokens
        self._auto_compact_threshold_tokens = auto_compact_threshold_tokens
        self._max_turns = max_turns
        self._permission_prompt = permission_prompt
        self._ask_user_prompt = ask_user_prompt
        self._ask_user_select = ask_user_select
        self._hook_executor = hook_executor
        self._tool_metadata = tool_metadata or {}
        self._messages: list[ConversationMessage] = []
        self._cost_tracker = CostTracker()
        # Iterate enforcement: auto-enable from kernel settings unless the
        # caller supplies an explicit policy. Passing ``False`` disables.
        self._iterate_policy: IterateLoopPolicy | None = (
            cast("IterateLoopPolicy | None", iterate_policy)
            if iterate_policy is not None
            else _default_iterate_policy(self._cwd)
        )
        # Reasoning effort: explicit argument wins, else the project config
        # (`iterate.config.yaml` `reasoning_effort`), else provider default.
        self._reasoning_effort = (
            reasoning_effort if reasoning_effort is not None else _default_reasoning_effort(self._cwd)
        )

    @property
    def messages(self) -> list[ConversationMessage]:
        """Return the current conversation history."""
        return list(self._messages)

    @property
    def max_turns(self) -> int | None:
        """Return the maximum number of agentic turns per user input, if capped."""
        return self._max_turns

    @property
    def api_client(self) -> SupportsStreamingMessages:
        """Return the active API client."""
        return self._api_client

    @property
    def model(self) -> str:
        """Return the active model identifier."""
        return self._model

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        return self._system_prompt

    @property
    def tool_metadata(self) -> dict[str, object]:
        """Return the mutable tool metadata/carry-over state."""
        return self._tool_metadata

    @property
    def iterate_policy(self) -> IterateLoopPolicy | None:
        """Return the attached iterate loop policy (``None`` when disabled)."""
        return self._iterate_policy

    @property
    def ask_user_select_channel(self) -> AskUserSelect | None:
        """Return the interactive select channel (``None`` when headless)."""
        return self._ask_user_select

    @property
    def ask_user_prompt_channel(self) -> AskUserPrompt | None:
        """Return the free-text question channel (``None`` when headless)."""
        return self._ask_user_prompt

    @property
    def total_usage(self) -> UsageSnapshot:
        """Return the total usage across all turns."""
        return self._cost_tracker.total

    def clear(self) -> None:
        """Clear the in-memory conversation history."""
        self._messages.clear()
        self._cost_tracker = CostTracker()

    def set_system_prompt(self, prompt: str) -> None:
        """Update the active system prompt for future turns."""
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        """Update the active model for future turns."""
        self._model = model

    def set_api_client(self, api_client: SupportsStreamingMessages) -> None:
        """Update the active API client for future turns."""
        self._api_client = api_client

    def set_max_turns(self, max_turns: int | None) -> None:
        """Update the maximum number of agentic turns per user input."""
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def set_permission_checker(self, checker: PermissionChecker) -> None:
        """Update the active permission checker for future turns."""
        self._permission_checker = checker

    def _build_coordinator_context_message(self) -> ConversationMessage | None:
        """Build a synthetic user message carrying coordinator runtime context."""
        context = get_coordinator_user_context()
        worker_tools_context = context.get("workerToolsContext")
        if not worker_tools_context:
            return None
        return ConversationMessage(
            role="user",
            content=[TextBlock(text=f"# Coordinator User Context\n\n{worker_tools_context}")],
        )

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        """Replace the in-memory conversation history."""
        self._messages = list(messages)

    def has_pending_continuation(self) -> bool:
        """Return True when the conversation ends with tool results awaiting a follow-up model turn."""
        if not self._messages:
            return False
        last = self._messages[-1]
        if last.role != "user":
            return False
        if not any(isinstance(block, ToolResultBlock) for block in last.content):
            return False
        for msg in reversed(self._messages[:-1]):
            if msg.role != "assistant":
                continue
            return bool(msg.tool_uses)
        return False

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[StreamEvent]:
        """Append a user message and execute the query loop."""
        if self._iterate_policy is not None:
            # A fresh submit is a fresh intent: drop any pause left over from
            # an earlier loop so it cannot fire on this run's round boundary.
            # (``iterate_policy=False`` explicitly disables iterate upstream.)
            clear_pause = getattr(self._iterate_policy, "clear_pause", None)
            if callable(clear_pause):
                clear_pause()
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        if user_message.text.strip() and not self._tool_metadata.pop("_suppress_next_user_goal", False):
            remember_user_goal(self._tool_metadata, user_message.text)
        self._messages.append(user_message)
        if self._hook_executor is not None:
            await self._hook_executor.execute(
                HookEvent.USER_PROMPT_SUBMIT,
                {
                    "event": HookEvent.USER_PROMPT_SUBMIT.value,
                    "prompt": user_message.text,
                },
            )
        context = QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            permission_checker=self._permission_checker,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            context_window_tokens=self._context_window_tokens,
            auto_compact_threshold_tokens=self._auto_compact_threshold_tokens,
            max_turns=self._max_turns,
            permission_prompt=self._permission_prompt,
            ask_user_prompt=self._ask_user_prompt,
            ask_user_select=self._ask_user_select,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            iterate_policy=self._iterate_policy if self._iterate_policy else None,
            reasoning_effort=self._reasoning_effort,
        )
        query_messages = list(self._messages)
        coordinator_context = self._build_coordinator_context_message()
        if coordinator_context is not None:
            query_messages.append(coordinator_context)
        async for event, usage in run_query(context, query_messages):
            if isinstance(event, AssistantTurnComplete):
                self._messages = list(query_messages)
            if usage is not None:
                self._cost_tracker.add(usage)
            yield event

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[StreamEvent]:
        """Continue an interrupted tool loop without appending a new user message."""
        context = QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            permission_checker=self._permission_checker,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            context_window_tokens=self._context_window_tokens,
            auto_compact_threshold_tokens=self._auto_compact_threshold_tokens,
            max_turns=max_turns if max_turns is not None else self._max_turns,
            permission_prompt=self._permission_prompt,
            ask_user_prompt=self._ask_user_prompt,
            ask_user_select=self._ask_user_select,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            iterate_policy=self._iterate_policy if self._iterate_policy else None,
            reasoning_effort=self._reasoning_effort,
        )
        async for event, usage in run_query(context, self._messages):
            if usage is not None:
                self._cost_tracker.add(usage)
            yield event
