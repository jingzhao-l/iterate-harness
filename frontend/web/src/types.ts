// Shared type definitions mirroring the backend Pydantic schemas.

export interface StatusResponse {
  project_root: string;
  last_run: {
    timestamp?: string;
    mode?: string;
    verdict?: string;
    rounds?: number;
    totalFindings?: number;
    severity?: Record<string, number>;
    preview?: Array<{
      severity: string;
      file: string;
      dimension: string;
      summary: string;
    }>;
    entryCount?: number;
    interrupted?: boolean;
  } | null;
  entry_count: number;
  latest_round: number;
  // Live fields merged from the SSE stream delta (SseStatusPayload) into the
  // REST snapshot. Optional because the plain GET /status response won't
  // always carry them.
  converged?: boolean | null;
  checkpoint_exists?: boolean;
  checkpoint_round?: number;
  convergence: number[];
  budget: {
    usedTokens: number;
    usedUsd: number;
    tokenBudget: number | null;
    budgetUsd: number | null;
    maxTurnsPerMinute: number | null;
    exhaustedDimensions: string[];
  };
  config: {
    mode: string;
    goal: string;
    maxRounds: number;
    language: string;
    dimensions: string[];
    worktreeIsolation: boolean;
    thresholdsConfigured: boolean;
  };
  reports: Array<{ name: string; path: string; size: number }>;
  audit_recent: Array<{
    timestamp: string;
    action: string;
    target: string;
    summary?: Record<string, unknown>;
  }>;
}

export interface RunSummary {
  index: number;
  timestamp: string;
  round: number;
  type: string;
  data: Record<string, unknown>;
}

export interface TimelineEntry {
  index: number;
  timestamp: string;
  round: number;
  type: string;
  data: Record<string, unknown>;
}

export interface Finding {
  dimension?: string;
  file?: string;
  severity?: string;
  summary?: string;
  failure_scenario?: string;
  suggested_fix?: string;
  is_atomic?: boolean;
  line?: number;
}

export interface FindingsResponse {
  findings: Finding[];
  /** Total findings matching the filters, before offset/limit are applied. */
  total: number;
  /** Number of findings actually returned in this page. */
  returned: number;
  /** Offset this page starts at. */
  offset: number;
  /** True when `total` exceeds the rows returned, so rows are missing. */
  truncated: boolean;
}

export interface CheckpointView {
  exists: boolean;
  checkpoint: Record<string, unknown> | null;
  last_report: {
    timestamp?: string;
    round?: number;
    verdict?: string;
    mode?: string;
    totalFindings?: number;
  } | null;
  interrupted: boolean;
}

export interface OperationResult {
  status: "ok" | "conflict" | "error";
  message: string;
  target?: string | null;
  detail?: Record<string, unknown> | null;
}

export interface ConfigView {
  exists: boolean;
  source: string;
  path: string;
  raw: Record<string, unknown>;
  effective: Record<string, unknown>;
  providers: Record<string, Record<string, unknown>>;
  active_profile: string;
  // Content hash of the on-disk config, echoed by GET /config and sent back on
  // PUT so a concurrent edit is rejected (409) instead of silently clobbered.
  version: string;
}

// Cross-run trend (design §17.3 P8). Mirrors iterate/trend_report.py.
export interface TrendFinding {
  file: string;
  line: number | null;
  dimension: string;
  severity: string;
  summary: string;
  fingerprint?: string;
  runs?: number;
  first_seen?: string;
  last_seen?: string;
}

export interface TrendRun {
  index: number;
  timestamp: string;
  round: number;
  verdict: string;
  mode: string;
  total: number;
  reported_total: number;
  new: number;
  fixed: number;
  regressed: number;
  unchanged: number;
  by_severity: Record<string, number>;
  by_dimension: Record<string, number>;
  new_findings?: TrendFinding[];
  fixed_findings?: TrendFinding[];
  regressed_findings?: TrendFinding[];
}

export interface TrendReport {
  project_root: string;
  generated_at: string;
  has_data: boolean;
  total_runs: number;
  tracked: number;
  open: number;
  fixed: number;
  stubborn: number;
  runs: TrendRun[];
  latest: TrendRun | null;
  open_findings: TrendFinding[];
  stubborn_findings: TrendFinding[];
  open_by_dimension: Record<string, number>;
}

export interface ReportView {
  name: string;
  path: string;
  size: number;
  modified?: string | null;
}

export interface ReportPreview {
  name: string;
  content: string;
  size: number;
}

// Workspaces (design §17.3 P4)
export interface WorkspaceView {
  name: string;
  path: string;
  kind: "primary" | "worktree";
  active: boolean;
  detail: {
    slug?: string;
    branch?: string;
    head?: string;
    dirty?: boolean;
    gitRoot?: string | null;
    isolationEnabled?: boolean;
    entryCount?: number;
    configExists?: boolean;
    agent_id?: string;
    created_at?: number;
    round?: number;
    stale?: boolean;
  };
}

// A persisted findings-triage decision (design §17.3 P2).
export interface TriageDecision {
  key: string;
  file: string;
  line: number | null;
  dimension: string;
  decision: "approve" | "reject";
  note: string | null;
  timestamp: string;
}

// Compact delta pushed over the SSE stream (events.py `_build_status_payload`).
// Kept separate from StatusResponse because the SSE payload uses camelCase.
export interface SseStatusPayload {
  entryCount: number;
  latestRound: number;
  checkpointExists: boolean;
  checkpointRound: number;
  totalTokens: number;
  totalCostUsd: number;
  converged: boolean | null;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// Chat / human-in-the-loop types (design §18)
// ---------------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: "system" | "assistant" | "user";
  // Kind: text / question / select / permission / progress / status / error / tool.
  kind: string;
  content: string;
  timestamp: string;
}

// "pausing"/"stopping" are pending transitions: the click registered, the
// effect lands at the next round boundary. Without them the badge kept
// reading "运行中" and the only acknowledgement was a 3-second toast.
export type ChatRunState =
  | "idle"
  | "starting"
  | "running"
  | "paused"
  | "pausing"
  | "stopping"
  | "stopped";
export type WaitingKind = "none" | "user_prompt" | "user_select" | "permission";

// Mirrors the backend ChatRunStatus (GET /chat/status, snake_case).
export interface ChatRunStatus {
  state: ChatRunState;
  run_id: string;
  mode: string;
  project_root: string;
  round: number;
  new_findings: number;
  total_findings: number;
  cost_usd: number;
  converged: boolean;
  waiting_for: WaitingKind;
  question: string | null;
  options: Array<{ value: string; label: string; description?: string }> | null;
  permission: { tool?: string; reason?: string } | null;
  /** The permission posture this run is under. Surfaced so the operator can
   * see whether the loop writes without asking. */
  permission_mode?: PermissionMode;
  /** Set when another process (usually the console) holds this project's run
   * lease. Without it the dashboard read "空闲 · 等待启动" for a project the
   * TUI was actively iterating, and invited a second writer. */
  driver?: RunDriver | null;
  error: string | null;
  message: string;
}

export interface RunDriver {
  role: string;
  pid: number;
  host: string;
  acquired_at: number;
}

export type PermissionMode = "full_auto" | "plan" | "default";

// "default" defers to the CLI/configured posture; "full_auto" does not ask
// before writing. It is explicit here because the WebUI previously hard-coded
// full_auto, so every WebUI run silently wrote without asking.
export interface StartRequest {
  mode: "review" | "run" | "resume";
  changed: boolean;
  ref: string;
  permission_mode?: PermissionMode;
}

// Partial hub events (camelCase) used to patch the store live.
export interface RunStateEvent {
  state?: ChatRunState;
  waitingFor?: WaitingKind;
  question?: string | null;
  options?: Array<{ value: string; label: string; description?: string }> | null;
  tool?: string;
  reason?: string;
  message?: string;
}

export interface ProgressUpdateEvent {
  round?: number;
  newFindings?: number;
  totalFindings?: number;
  costUsd?: number;
  converged?: boolean;
  mode?: string;
}

// ---- Agent roster (design §17.3 P9) ----
// One row per worker the harness is tracking. Every backend writes the same
// store, so a subprocess teammate, an in-process teammate and a plain shell
// task all show up here — which is what the page exists to make visible.

export type AgentStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "killed"
  | "unknown";

export interface AgentRow {
  id: string;
  agent_id: string;
  team: string;
  kind: string;
  backend: string;
  task_type: string;
  status: AgentStatus;
  running: boolean;
  description: string;
  status_note: string;
  progress: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_s: number;
  return_code: number | null;
  output_bytes: number;
  tail: string;
  cwd: string;
}

export interface AgentsView {
  generated_at: string;
  total: number;
  truncated: boolean;
  running: number;
  failed: number;
  killed: number;
  completed: number;
  agents: AgentRow[];
}

export type StopOutcome =
  | "stopped"
  | "not_found"
  | "already_terminal"
  | "error";

export interface StopAgentResult {
  status: StopOutcome;
  message: string;
  id: string | null;
  agent_id: string;
  status_now: string | null;
  return_code: number | null;
}

// ---- Recipe gate (design §17.3 P9) ----
// The ④ answer a round publishes when it measured an invariant verdict. The
// CLI has printed this since the GlassPane hand-off; the console now reads the
// same records. `has_gate: false` means "nobody asked", never "it passed".

export interface GateFixProof {
  entry_id: string;
  operation_id: string;
  outcome: string;
  ledger: string;
  ledger_source: string;
  created_at: string;
}

export interface GateRefusal {
  code: string;
  message: string;
  remedy: string;
}

export interface GateRecord {
  round: number;
  timestamp: string;
  allowed: boolean;
  invariant_verdict: string;
  recipe_digest: string | null;
  steps: string[];
  fix_proof: GateFixProof | null;
  refusal: GateRefusal | null;
}

export interface GateView {
  project_root: string;
  generated_at: string;
  has_gate: boolean;
  total: number;
  latest: GateRecord | null;
  history: GateRecord[];
  rendered: string;
}

// ---- Scheduled reviews (design §17.3 P10) ----
// The cron registry and daemon are machine-global (not per project), so this
// view carries no project root. Times are ISO-8601 with an offset.

export interface SchedulerStatus {
  running: boolean;
  pid: number | null;
  total_jobs: number;
  enabled_jobs: number;
  log_file: string;
  history_file: string;
}

export interface CronJobView {
  name: string;
  schedule: string;
  timezone: string | null;
  command: string;
  cwd: string;
  enabled: boolean;
  schedule_valid: boolean;
  next_run: string | null;
  last_run: string | null;
  last_status: string | null;
}

export interface CronHistoryView {
  name: string;
  command: string;
  started_at: string;
  ended_at: string;
  status: string;
  returncode: number | null;
  duration_s: number | null;
  stdout_tail: string;
  stderr_tail: string;
}

export interface SchedulesView {
  generated_at: string;
  scheduler: SchedulerStatus;
  jobs: CronJobView[];
  history: CronHistoryView[];
}

// ---- Two-run comparison (design §17.3 P2) ----
// `runs` lists every finished run (for the pickers); `from_run`/`to_run` are
// null until the project has two. Finding lists are worst-first.

export interface RunSnapshotView {
  index: number;
  timestamp: string;
  round: number;
  verdict: string;
  mode: string;
  total: number;
}

export interface DiffFinding {
  file: string;
  line: number | null;
  dimension: string;
  severity: string;
  summary: string;
  fingerprint?: string;
}

export interface RunDiffCounts {
  new: number;
  fixed: number;
  regressed: number;
  unchanged: number;
}

export interface RunDiffView {
  project_root: string;
  generated_at: string;
  runs: RunSnapshotView[];
  from_run: RunSnapshotView | null;
  to_run: RunSnapshotView | null;
  counts: RunDiffCounts;
  new: DiffFinding[];
  fixed: DiffFinding[];
  regressed: DiffFinding[];
  unchanged: DiffFinding[];
}
