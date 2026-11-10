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
"""``conflict`` is reserved for concurrent-edit outcomes (e.g. a stale
config version); routes currently surface conflicts as HTTP 409 with a
``{"detail"}`` body instead, so only ``ok``/``error`` are produced today."""

#: Live iterate loop states (design §18.3 run state machine).
#:
#: ``pausing`` / ``stopping`` are the *pending-transition* states. A pause or a
#: stop only takes effect at the next round boundary or task-cancel, which can
#: be minutes away; without a state for the request, the badge kept reading
#: "运行中" after the operator clicked, and the answer to "did my click
#: register?" was a toast that vanished in three seconds.
RunState = Literal[
    "idle", "starting", "running", "paused", "pausing", "stopping", "stopped"
]

#: What kind of user input the loop is currently waiting for.
WaitingKind = Literal["none", "user_prompt", "user_select", "permission"]


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


class TrendView(BaseModel):
    """Cross-run finding trend (design §17.3 P8)."""

    project_root: str
    generated_at: str
    has_data: bool
    total_runs: int
    tracked: int
    open: int
    fixed: int
    stubborn: int
    runs: list[dict[str, Any]] = Field(default_factory=list)
    latest: dict[str, Any] | None = None
    open_findings: list[dict[str, Any]] = Field(default_factory=list)
    stubborn_findings: list[dict[str, Any]] = Field(default_factory=list)
    open_by_dimension: dict[str, int] = Field(default_factory=dict)


class GateView(BaseModel):
    """Recipe-gate answers published to this project's decision log (design §17.3 P9).

    ``has_gate`` false means no round recorded one — absence, not a pass.
    ``rendered`` is the exact CLI text for the latest answer.
    """

    project_root: str
    generated_at: str
    has_gate: bool
    total: int
    latest: dict[str, Any] | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    rendered: str


class SchedulesView(BaseModel):
    """Machine-level scheduled reviews and their execution history (design §17.3 P10).

    The cron registry and daemon are global to this machine, not scoped to a
    project, so this view carries no project root.
    """

    generated_at: str
    scheduler: dict[str, Any] = Field(default_factory=dict)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)


class DoctorView(BaseModel):
    """Environment self-check report (design §17.3 P11).

    ``checks`` is ordered failures/warnings first so a broken environment is
    visible at the top of the console page. Each check carries an executable
    ``remedy`` the operator can copy — never a stack trace, never a
    credential.
    """

    project_root: str
    generated_at: str
    ok: bool
    has_failures: bool
    has_warnings: bool
    summary: dict[str, int] = Field(default_factory=dict)
    checks: list[dict[str, Any]] = Field(default_factory=list)


class CapabilityProblem(BaseModel):
    """One reason a capability is missing or misconfigured (design §17.3 P12)."""

    kind: str
    name: str
    detail: str
    remedy: str = ""


class CapabilityTool(BaseModel):
    """A tool the engine registers for this project."""

    name: str
    description: str = ""
    required_args: list[str] = Field(default_factory=list)
    optional_args: list[str] = Field(default_factory=list)


class CapabilityCommand(BaseModel):
    """A slash command registered in this process."""

    name: str
    description: str = ""
    behavior: str = "unknown"
    behavior_detail: str = ""
    remote_invocable: bool = True
    remote_admin_opt_in: bool = False


class CapabilitySkill(BaseModel):
    """A bundled, user or plugin skill visible to this project."""

    name: str
    description: str = ""
    source: str = ""
    path: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    model: str = ""


class CapabilityPlugin(BaseModel):
    """One discovered plugin and the artifacts it contributes."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    enabled: bool = True
    path: str = ""
    skills: int = 0
    commands: int = 0
    agents: int = 0
    mcp_servers: int = 0


class CapabilityMcpServer(BaseModel):
    """One configured MCP server with its static validation result."""

    name: str
    transport: str = "unknown"
    target: str = "configured"
    status: str = "ok"
    issues: list[str] = Field(default_factory=list)
    remedy: str = ""


class CapabilitiesView(BaseModel):
    """Read-only capability inventory (design §17.3 P12).

    It is the console/CLI projection of the exact registries the engine loads.
    ``has_problems`` is true when a loader failed or an MCP server cannot start
    as configured; ``summary`` still counts everything that loaded.
    """

    project_root: str
    generated_at: str
    summary: dict[str, int] = Field(default_factory=dict)
    has_problems: bool = False
    problems: list[CapabilityProblem] = Field(default_factory=list)
    tools: list[CapabilityTool] = Field(default_factory=list)
    commands: list[CapabilityCommand] = Field(default_factory=list)
    skills: list[CapabilitySkill] = Field(default_factory=list)
    plugins: list[CapabilityPlugin] = Field(default_factory=list)
    mcp_servers: list[CapabilityMcpServer] = Field(default_factory=list)
    hooks: list[dict[str, Any]] = Field(default_factory=list)


class RunSummary(BaseModel):
    """One decision-log entry in the runs overview (design §17.3 P2)."""

    index: int
    timestamp: str
    round: int
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunDiffView(BaseModel):
    """Two finished runs compared by finding fingerprint (design §17.3 P2).

    ``runs`` carries every finished run's metadata so the console can build its
    pickers from one response; the finding lists are worst-first.
    """

    project_root: str
    generated_at: str
    runs: list[dict[str, Any]] = Field(default_factory=list)
    from_run: dict[str, Any] | None = None
    to_run: dict[str, Any] | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    new: list[dict[str, Any]] = Field(default_factory=list)
    fixed: list[dict[str, Any]] = Field(default_factory=list)
    regressed: list[dict[str, Any]] = Field(default_factory=list)
    unchanged: list[dict[str, Any]] = Field(default_factory=list)


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
    version: str = ""
    """Content hash of the on-disk config, used for optimistic concurrency.

    ``PUT /config`` accepts the version the editor loaded; if the file changed
    on disk in the meantime the write is rejected with ``409`` instead of
    silently clobbering a concurrent edit (e.g. the operator hand-edited the
    YAML in their editor while the WebUI form was open). Empty when the file
    does not exist yet.
    """


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


class WorkspaceRemoveRequest(BaseModel):
    """Request body for removing a stale isolate worktree (design §17.3 P4)."""

    slug: str = Field(..., min_length=1, max_length=64)


class FindingsTriageRequest(BaseModel):
    """Request body for triaging one finding (design §17.3 P2).

    ``decision`` is ``approve`` (accept the finding / its suggested fix) or
    ``reject`` (false positive — skip). The (file, line, dimension) triple
    is the dedup key mirroring ``runs.get_findings``.
    """

    file: str = Field(..., min_length=1)
    line: int | None = None
    dimension: str = ""
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class FindingsTriageDismissRequest(BaseModel):
    """Request body for dismissing one persisted triage decision.

    Identifies the finding by its (file, line, dimension) dedup key — the same
    triple used by :class:`FindingsTriageRequest` and ``runs.get_findings``.
    """

    file: str = Field(..., min_length=1)
    line: int | None = None
    dimension: str = ""


class ScheduleUpsertRequest(BaseModel):
    """Request body for registering/updating the scheduled quick review.

    Mirrors ``ih iterate schedule add``: the console may only create the
    canonical ``iterate.review-changed`` job, for a project root the console
    is already allowed to serve. ``replace`` acknowledges that an existing
    job pointed at a *different* directory will be moved — without it the
    route answers 409 instead of silently retargeting someone's schedule.
    """

    project_root: str = ""
    schedule: str = Field(..., min_length=1, max_length=120)
    ref: str = Field(default="HEAD", min_length=1, max_length=200)
    rounds: int = Field(default=3, ge=1, le=20)
    mode: Literal["dry-run", "normal"] = "dry-run"
    timezone: str = Field(default="", max_length=64)
    timeout: int = Field(default=3600, ge=60, le=7200)
    replace: bool = False


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
    #: Human-in-the-loop posture for this run. Defaults to ``default`` (defer
    #: to the configured/credential mode) so launching a loop from the
    #: auxiliary channel cannot silently escalate a console the operator
    #: configured to ask before writing. ``full_auto`` writes without asking;
    #: ``plan`` pauses on every write.
    permission_mode: Literal["full_auto", "plan", "default"] = "default"


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
    #: The permission posture this run is actually under. It was tracked in the
    #: manager but never surfaced, so an operator could not tell that a run they
    #: launched from the WebUI was in ``full_auto`` — i.e. that the auxiliary
    #: channel had silently granted the loop write access without asking.
    #: Default matches the run manager's real default (``"default"``); claiming
    #: ``full_auto`` for a status built without an explicit value would mislabel
    #: a run that actually asks before every mutation.
    permission_mode: str = "default"
    #: Set when *another* process (typically the console) holds this project's
    #: run lease. The WebUI used to report ``idle`` in that case and offer to
    #: start a competing loop; this tells the operator who actually owns it.
    driver: RunDriver | None = None


class RunDriver(BaseModel):
    """The other front end that currently owns a project's iterate loop."""

    role: str = "unknown"
    """``console`` (CLI/TUI) or ``webui``."""

    pid: int = 0
    host: str = ""
    acquired_at: float = 0.0
    """Unix seconds when that process took the lease."""


#: One row of the agent roster — one worker the harness is currently
#: tracking, from any backend.
class AgentRow(BaseModel):
    """A single tracked worker (subprocess teammate, in-process teammate,
    or shell task) plus a bounded tail of what it last emitted."""

    id: str
    agent_id: str = ""
    """Swarm identity (``name@team``); empty for plain shell tasks."""
    team: str = ""
    kind: str = "other"
    """``subprocess`` / ``in_process`` / ``shell`` / ``other``."""
    backend: str = "subprocess"
    task_type: str = ""
    status: str = "unknown"
    running: bool = False
    description: str = ""
    status_note: str = ""
    progress: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float = 0.0
    return_code: int | None = None
    output_bytes: int = 0
    tail: str = ""
    cwd: str = ""


class AgentsView(BaseModel):
    """Roster of every worker the harness is tracking (design §17.3 P9)."""

    generated_at: str = ""
    total: int = 0
    truncated: bool = False
    running: int = 0
    failed: int = 0
    killed: int = 0
    completed: int = 0
    agents: list[AgentRow] = Field(default_factory=list)


#: Outcomes of a stop request. ``not_found`` / ``already_terminal`` are
#: truthful operator-facing answers, not server failures; ``error`` means the
#: manager raised, with the real exception in ``message``.
StopStatus = Literal["stopped", "not_found", "already_terminal", "error"]


class StopAgentResult(BaseModel):
    """Result of stopping one agent from the console."""

    status: StopStatus
    message: str
    id: str | None = None
    agent_id: str = ""
    status_now: str | None = None
    return_code: int | None = None


class AuditEntry(BaseModel):
    """One persisted audit-journal record (design §17.3 P13).

    The journal is append-only; this is the read-side projection shared by
    ``ih audit`` and ``GET /api/v1/audit``. ``summary`` is a free-form mapping
    of operation parameters (already redacted server-side on write).
    """

    timestamp: str = ""
    action: str = ""
    target: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


class AuditView(BaseModel):
    """Paged, filterable audit trail (design §17.3 P13).

    ``entries`` is newest-first; ``total`` is the count of *matching* entries
    before pagination, so the page can say "showing m of n" and page correctly.
    ``actions`` lists every distinct action prefix seen, for the filter
    dropdown; ``truncated`` is True when more matching entries exist beyond the
    page (offset + len(entries) < total).
    """

    project_root: str
    generated_at: str
    total: int = 0
    returned: int = 0
    offset: int = 0
    limit: int = 50
    truncated: bool = False
    actions: list[str] = Field(default_factory=list)
    entries: list[AuditEntry] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    """One persistent memory file (design §17.3 P14)."""

    name: str
    title: str
    description: str = ""
    type: str = ""
    modified_at: str = ""
    body_preview: str = ""
    bytes: int = 0
    path: str = ""


class MemoryEntryPoint(BaseModel):
    """The project's MEMORY.md index."""

    name: str
    path: str
    exists: bool = False
    lines: int = 0
    bytes: int = 0
    preview: str = ""
    error: str | None = None


class MemoryInjectionFile(BaseModel):
    """One memory file as it appears in the prompt injection."""

    name: str
    title: str = ""
    chars_total: int = 0
    chars_included: int = 0
    truncated: bool = False


class MemoryInjectionView(BaseModel):
    """What the runtime prompt would inject for a given task (design §17.3 P14).

    Built by the same code path as the prompt itself, so the preview cannot
    drift from the model's actual context. ``truncated_files`` lists the
    memories that were clipped by the per-file character budget.
    """

    enabled: bool
    prompt: str = ""
    rendered: str = ""
    chars: int = 0
    files: int = 0
    max_files: int = 0
    max_entry_chars: int = 0
    entrypoint_lines_total: int = 0
    entrypoint_lines_included: int = 0
    entrypoint_truncated: bool = False
    truncated_files: list[dict[str, Any]] = Field(default_factory=list)
    included_files: list[MemoryInjectionFile] = Field(default_factory=list)
    reason: str = ""


class MemoryProblem(BaseModel):
    """A broken memory file or a dead MEMORY.md link."""

    name: str
    detail: str
    remedy: str = ""


class MemoryView(BaseModel):
    """Read-only project memory inventory (design §17.3 P14).

    The engine injects this bank into every runtime prompt; the console and
    ``ih memory`` share this model. ``matches`` is rank-ordered by the
    engine's relevance scorer when ``query`` is set; ``injection`` is present
    when ``prompt`` is set and carries the exact rendered prompt section.
    """

    project_root: str
    generated_at: str
    memory_dir: str
    exists: bool
    query: str = ""
    prompt: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    entrypoint: MemoryEntryPoint
    entries: list[MemoryEntry] = Field(default_factory=list)
    matches: list[MemoryEntry] = Field(default_factory=list)
    injection: MemoryInjectionView | None = None
    problems: list[MemoryProblem] = Field(default_factory=list)


class MemoryCreateRequest(BaseModel):
    """Create/overwrite a memory file from the console (design §17.3 P14)."""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    overwrite: bool = False


class HookDefinitionRow(BaseModel):
    """One registered lifecycle hook (design §17.3 P15)."""

    event: str = ""
    type: str = ""
    raw_type: str = ""
    matcher: str = ""
    detail: str = ""
    command: str = ""
    prompt: str = ""
    url: str = ""
    model: str = ""
    timeout_seconds: int = 30
    block_on_failure: bool = False
    source: str = "settings"
    headers: dict[str, str] = Field(default_factory=dict)


class HookEventGroup(BaseModel):
    """All hooks registered for one lifecycle event."""

    event: str
    count: int = 0
    hooks: list[HookDefinitionRow] = Field(default_factory=list)


class HookLogEntry(BaseModel):
    """One persisted hook-execution journal record."""

    timestamp: str = ""
    event: str = ""
    hook_type: str = ""
    matcher: str = ""
    detail: str = ""
    model: str = ""
    success: bool = False
    blocked: bool = False
    reason: str = ""
    output: str = ""
    duration_ms: int = 0
    returncode: int | None = None
    status_code: int | None = None
    source: str = "settings"


class HooksView(BaseModel):
    """Read-only lifecycle-hook inventory (design §17.3 P15).

    The engine loads these hooks and runs them inline in front of tool calls
    and turn boundaries; the console and ``ih hooks list`` share this model.
    ``has_problems`` is true when a hook event name is unknown (which the
    loader would silently drop) or a loader failed; ``journal`` aggregates the
    project's hook-execution trail.
    """

    project_root: str
    generated_at: str
    summary: dict[str, Any] = Field(default_factory=dict)
    has_problems: bool = False
    problems: list[CapabilityProblem] = Field(default_factory=list)
    events: list[HookEventGroup] = Field(default_factory=list)
    journal: dict[str, Any] = Field(default_factory=dict)
    journal_events: list[str] = Field(default_factory=list)
    journal_types: list[str] = Field(default_factory=list)


class HookLogView(BaseModel):
    """Paged, filterable hook-execution trail (design §17.3 P15).

    ``entries`` is newest-first; ``total`` is the count of *matching* entries
    before pagination, so the page can say "showing m of n" and page correctly.
    ``events`` / ``types`` list the distinct values seen, for the filter
    dropdowns.
    """

    project_root: str
    generated_at: str
    total: int = 0
    returned: int = 0
    offset: int = 0
    limit: int = 50
    truncated: bool = False
    events: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    entries: list[HookLogEntry] = Field(default_factory=list)


__all__ = [
    "AgentRow",
    "AgentsView",
    "AuditEntry",
    "AuditView",
    "CapabilityProblem",
    "CapabilitiesView",
    "ChatMessage",
    "ChatRunStatus",
    "CheckpointView",
    "ConfigView",
    "ControlRequest",
    "DoctorView",
    "FindingsTriageDismissRequest",
    "FindingsTriageRequest",
    "HookDefinitionRow",
    "HookEventGroup",
    "HookLogEntry",
    "HookLogView",
    "HooksView",
    "MemoryCreateRequest",
    "MemoryEntry",
    "MemoryEntryPoint",
    "MemoryInjectionFile",
    "MemoryInjectionView",
    "MemoryProblem",
    "MemoryView",
    "OperationResult",
    "ReportView",
    "RunDriver",
    "RunState",
    "RunSummary",
    "SendMessageRequest",
    "SelectOption",
    "StartRequest",
    "StopAgentResult",
    "StopStatus",
    "StatusResponse",
    "TimelineEntry",
    "WaitingKind",
    "WorkspaceRemoveRequest",
    "WorkspaceView",
]
