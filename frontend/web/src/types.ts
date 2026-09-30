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
  /** Total findings on disk *before* pagination/filtering. */
  total: number;
  /** Number of findings actually returned in this page (not a page number). */
  page: number;
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
