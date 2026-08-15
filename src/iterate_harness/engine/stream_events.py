"""Events yielded by the query engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.engine.messages import ConversationMessage


@dataclass(frozen=True)
class AssistantTextDelta:
    """Incremental assistant text."""

    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    """Completed assistant turn."""

    message: ConversationMessage
    usage: UsageSnapshot


@dataclass(frozen=True)
class ToolExecutionStarted:
    """The engine is about to execute a tool."""

    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionCompleted:
    """A tool has finished executing."""

    tool_name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class ErrorEvent:
    """An error that should be surfaced to the user."""

    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class StatusEvent:
    """A transient system status message shown to the user."""

    message: str


@dataclass(frozen=True)
class CompactProgressEvent:
    """Structured progress event for conversation compaction."""

    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ]
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = None
    attempt: int | None = None
    checkpoint: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReviewProgressEvent:
    """Structured progress event for the iterate review loop.

    Emitted by the engine's iterate control block (via
    ``QueryContext.iterate_policy``) each time a new deterministic aggregate
    lands, carrying per-round findings, per-dimension counts, and the
    running USD cost from the iterate money layer.
    """

    round: int
    new_findings: int
    total_findings: int
    per_dimension: dict[str, int]
    converged: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    mode: str = "dry-run"
    # Per-dimension estimated USD (reviewer-reported accounting; empty when
    # no dimension usage was reported). NOT part of ``cost_usd``.
    dimension_cost_usd: dict[str, float] = field(default_factory=dict)


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ErrorEvent
    | StatusEvent
    | CompactProgressEvent
    | ReviewProgressEvent
)
