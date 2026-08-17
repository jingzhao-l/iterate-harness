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


__all__ = [
    "CheckpointView",
    "ConfigView",
    "ErrorResponse",
    "OperationResult",
    "ReportView",
    "RunSummary",
    "StatusResponse",
    "TimelineEntry",
    "WorkspaceView",
]
