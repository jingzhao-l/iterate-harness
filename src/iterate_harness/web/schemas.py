"""Pydantic response models for the WebUI management console (design §17.4).

Keeping explicit models makes the API contract self-documenting and lets
FastAPI emit JSON Schema + OpenAPI for the frontend's ``fetch`` client.
All models are read-side views over the ``iterate`` data layer; mutating
operations return :class:`OperationResult` with a machine-readable status.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Operation outcome values used across mutating endpoints.
OperationStatus = Literal["ok", "conflict", "error"]

#: Live iterate loop states (design §18.3 run state machine).
RunState = Literal["idle", "starting", "running", "paused", "stopped"]

#: What kind of user input the loop is currently waiting for.
WaitingKind = Literal["none", "user_prompt", "user_select", "permission"]

#: HTTP statuses the API may return (mirrors design §17.4 error contract).
ErrorStatus = Literal[400, 401, 404, 409, 422, 500]


class ErrorResponse(BaseModel):
    """Uniform error payload."""

    error: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable message")
    detail: dict[str, Any] | None = None


class OperationResult(BaseModel):
    """Result of a mutating web operation."""

    status: OperationStatus
    message: str
    target: str | None = None
    detail: dict[str, Any] | None = None


class StatusResponse(BaseModel):
    """Dashboard aggregate (design §17.3 P1)."""

    project_root: str
    last_run: dict[str, Any] | None = None
    entry_count: int = 0
    latest_round: int = 0
    convergence: list[int] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    audit_recent: list[dict[str, Any]] = Field(default_factory=list)


class RunSummary(BaseModel):
    """One decision-log entry in the runs overview (design §17.3 P2)."""

    index: int
    timestamp: str
    round: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class TimelineEntry(BaseModel):
    """One trajectory entry inside a run's timeline."""

    index: int
    timestamp: str
    round: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class CheckpointView(BaseModel):
    """Checkpoint state plus context (design §17.3 P3)."""

    exists: bool
    checkpoint: dict[str, Any] | None = None
    last_report: dict[str, Any] | None = None
    interrupted: bool = False


class ConfigView(BaseModel):
    """Configuration read view with credentials redacted (design §17.3 P6)."""

    exists: bool
    source: str
    path: str
    raw: dict[str, Any] = Field(default_factory=dict)
    effective: dict[str, Any] = Field(default_factory=dict)
    providers: dict[str, Any] = Field(default_factory=dict)
    active_profile: str = ""


class ReportView(BaseModel):
    """A report file entry on the Reports page (design §17.3 P7)."""

    name: str
    path: str
    size: int
    modified: str | None = None


class WorkspaceView(BaseModel):
    """A workspace entry on the Workspaces page (design §17.3 P4)."""

    name: str
    path: str
    kind: str
    active: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Chat / human-in-the-loop models (design §18.3)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """One chat-panel entry (system status, assistant question, user input)."""

    id: str
    role: Literal["system", "assistant", "user"]
    #: Message kind: ``text`` / ``question`` / ``select`` / ``permission`` /
    #: ``progress`` / ``status`` / ``error`` / ``tool``.
    kind: str = "text"
    content: str
    timestamp: str


class StartRequest(BaseModel):
    """Request body for starting an iterate loop (design §18.3)."""

    mode: Literal["review", "run", "resume"] = "review"
    changed: bool = False
    ref: str = "HEAD"


class SendMessageRequest(BaseModel):
    """Request body for sending a chat message (answer or nudge)."""

    content: str = Field(..., min_length=1, max_length=8000)


class SelectOption(BaseModel):
    """One selectable option in a pause menu (mirrors the engine's shape).

    Field names match :func:`iterate_harness.iterate.prompts.pause_menu_options`
    (``value`` / ``label`` / ``description``), which the frontend renders as
    quick-action buttons in the chat panel (design §18.4).
    """

    value: str
    label: str
    description: str | None = None


class ControlRequest(BaseModel):
    """Request body for a run control command (pause/resume/stop)."""

    action: Literal["pause", "resume", "stop"]


class ChatRunStatus(BaseModel):
    """Live run status snapshot for the chat panel (design §18.3)."""

    state: RunState
    run_id: str = ""
    mode: str = ""
    project_root: str = ""
    round: int = 0
    new_findings: int = 0
    total_findings: int = 0
    cost_usd: float = 0.0
    converged: bool = False
    waiting_for: WaitingKind = "none"
    question: str | None = None
    options: list[SelectOption] | None = None
    permission: dict[str, Any] | None = None
    error: str | None = None
    message: str = ""


__all__ = [
    "ChatMessage",
    "ChatRunStatus",
    "CheckpointView",
    "ConfigView",
    "ControlRequest",
    "ErrorResponse",
    "OperationResult",
    "ReportView",
    "RunState",
    "RunSummary",
    "SendMessageRequest",
    "SelectOption",
    "StartRequest",
    "StatusResponse",
    "TimelineEntry",
    "WaitingKind",
    "WorkspaceView",
]
