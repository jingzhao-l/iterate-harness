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
  total: number;
  page: number;
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

export type ChatRunState = "idle" | "starting" | "running" | "paused" | "stopped";
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
  error: string | null;
  message: string;
}

export interface StartRequest {
  mode: "review" | "run" | "resume";
  changed: boolean;
  ref: string;
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
