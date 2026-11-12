// Thin fetch wrapper over the /api/v1 backend (design §17.5).
// No UI framework, no axios — plain fetch with JSON handling + error decode.

import type {
  AgentsView,
  AuditExportFormat,
  AuditView,
  CapabilitiesView,
  ChatMessage,
  ChatRunStatus,
  CheckpointView,
  ConfigView,
  DoctorView,
  FindingsResponse,
  GateView,
  HookLogView,
  HooksView,
  MemoryCreateInput,
  MemoryEntryDetail,
  MemoryView,
  OperationResult,
  ReportPreview,
  ReportView,
  RunDiffView,
  RunSummary,
  SchedulesView,
  ScheduleUpsertInput,
  StartRequest,
  StatusResponse,
  StopAgentResult,
  TimelineEntry,
  TrendReport,
  TriageDecision,
  WorkspaceView,
} from "./types";

const API_BASE = "/api/v1";

// ---- WebUI access token (design §17.4) ----
// The backend serve() opens the browser with `?token=<token>` in the URL and
// protects every /api/v1 route with it. We capture it on first load, cache it
// in sessionStorage (so reloads of the same tab keep working), strip it from
// the address bar so it never lingers in history / share links, and attach it
// to every request: `Authorization: Bearer` for fetch, `?token=` for the
// EventSource stream (which cannot set custom headers).

const TOKEN_STORAGE_KEY = "iterate.webui.token";

function captureToken(): string {
  const urlToken =
    new URLSearchParams(window.location.search).get("token") ?? "";
  if (urlToken) {
    sessionStorage.setItem(TOKEN_STORAGE_KEY, urlToken);
    const url = new URL(window.location.href);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
    return urlToken;
  }
  return sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
}

const WEBUI_TOKEN = captureToken();

/** The WebUI access token, or "" when authentication is disabled. */
export function webuiToken(): string {
  return WEBUI_TOKEN;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function withAuth(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (WEBUI_TOKEN) headers.set("Authorization", `Bearer ${WEBUI_TOKEN}`);
  return { ...init, headers };
}

/** Decode a non-2xx response into an ApiError carrying the backend's text. */
async function throwApiError(response: Response): Promise<never> {
  let message = `HTTP ${response.status}`;
  let detail: unknown = undefined;
  try {
    const body = (await response.json()) as { message?: string; detail?: unknown };
    // FastAPI reports human-readable failures under ``detail`` (a string for
    // HTTPException, a list of issues for a 422). Every backend error message
    // was being dropped on the floor, so the UI showed "控制失败：HTTP 409"
    // with no indication of what to do — including the 401 that names the
    // exact recovery command. Prefer whichever field actually carries text.
    if (typeof body.detail === "string" && body.detail) {
      message = body.detail;
    } else if (Array.isArray(body.detail) && body.detail.length > 0) {
      message = body.detail
        .map((issue) => {
          const entry = issue as { msg?: unknown; loc?: unknown };
          const loc = Array.isArray(entry.loc) ? entry.loc.join(".") : "";
          const text = typeof entry.msg === "string" ? entry.msg : JSON.stringify(entry);
          return loc ? `${loc}: ${text}` : text;
        })
        .join("; ");
    } else if (body.message) {
      message = body.message;
    }
    detail = body.detail;
  } catch {
    // non-JSON error body; keep default message
  }
  throw new ApiError(response.status, message, detail);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, withAuth(init));
  if (!response.ok) await throwApiError(response);
  // 204 / empty responses
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// Build a query string including the optional project_root plus any extra
// params. Omits undefined/empty values so the resulting URL is always valid
// (no dangling "?" or "&" when nothing is set).
function buildQuery(
  projectRoot: string | undefined,
  extra?: Record<string, string | number | undefined>,
): string {
  const params = new URLSearchParams();
  if (projectRoot) params.set("project_root", projectRoot);
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export type TrendExportFormat = "md" | "csv" | "json";

export interface TrendExportDownload {
  filename: string;
  text: string;
  contentType: string;
}

export interface AuditExportDownload {
  filename: string;
  text: string;
  contentType: string;
}

/**
 * Fetch a trend export as text plus the server's filename. The server owns the
 * filename (it stamps the report's own generated_at), so the download matches
 * the data even when the browser clock is off.
 */
export async function downloadTrendExport(
  projectRoot: string | undefined,
  format: TrendExportFormat,
  top = 20,
  history = 20,
): Promise<TrendExportDownload> {
  const response = await fetch(
    `${API_BASE}/trend/export${buildQuery(projectRoot, { format, top, history })}`,
    withAuth(),
  );
  if (!response.ok) await throwApiError(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return {
    filename: match?.[1] ?? `iterate-trend.${format}`,
    text: await response.text(),
    contentType: response.headers.get("Content-Type") ?? "",
  };
}

export type HookExportFormat = "md" | "csv" | "json";

export interface HookExportDownload {
  filename: string;
  text: string;
  contentType: string;
}

export interface HookLogQuery {
  event?: string;
  hookType?: string;
  success?: string;
}

/**
 * Download the filtered hook-execution trail as Markdown / CSV / JSON.
 * Unlike the page it is not paginated: a diagnostic export carries the whole
 * filtered window. The server owns the filename (it stamps the export time).
 */
export async function downloadHookLogExport(
  projectRoot: string | undefined,
  format: HookExportFormat,
  query: HookLogQuery = {},
): Promise<HookExportDownload> {
  const response = await fetch(
    `${API_BASE}/hooks/log/export${buildQuery(projectRoot, {
      format,
      event: query.event,
      hook_type: query.hookType,
      success: query.success,
    })}`,
    withAuth(),
  );
  if (!response.ok) await throwApiError(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return {
    filename: match?.[1] ?? `iterate-hooks.${format}`,
    text: await response.text(),
    contentType: response.headers.get("Content-Type") ?? "",
  };
}

export interface AuditQuery {
  action?: string;
  target?: string;
  since?: string;
  offset?: number;
  limit?: number;
}

/**
 * Fetch the paged audit trail (newest first). Read-only; the journal is
 * append-only and the same reader backs `ih audit`.
 */
export async function fetchAudit(
  projectRoot: string | undefined,
  query: AuditQuery = {},
): Promise<AuditView> {
  return request<AuditView>(
    `/audit${buildQuery(projectRoot, {
      action: query.action,
      target: query.target,
      since: query.since,
      offset: query.offset,
      limit: query.limit,
    })}`,
  );
}

/** Download the filtered audit trail as Markdown / CSV / JSON. */
export async function downloadAuditExport(
  projectRoot: string | undefined,
  format: AuditExportFormat,
  query: AuditQuery = {},
): Promise<AuditExportDownload> {
  const response = await fetch(
    `${API_BASE}/audit/export${buildQuery(projectRoot, {
      format,
      action: query.action,
      target: query.target,
      since: query.since,
    })}`,
    withAuth(),
  );
  if (!response.ok) await throwApiError(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return {
    filename: match?.[1] ?? `iterate-audit.${format}`,
    text: await response.text(),
    contentType: response.headers.get("Content-Type") ?? "",
  };
}

export const api = {
  status: (projectRoot?: string): Promise<StatusResponse> =>
    request<StatusResponse>(`/status${buildQuery(projectRoot)}`),

  listRuns: (projectRoot?: string, offset = 0, limit = 50): Promise<RunSummary[]> =>
    request<RunSummary[]>(
      `/runs${buildQuery(projectRoot, { offset, limit })}`,
    ),

  timeline: (
    projectRoot?: string,
    round?: number,
    type?: string,
    limit = 200,
    offset = 0,
  ): Promise<TimelineEntry[]> =>
    request<TimelineEntry[]>(
      `/runs/timeline${buildQuery(projectRoot, {
        round: round !== undefined && round >= 0 ? round : undefined,
        type,
        limit,
        offset,
      })}`,
    ),

  findings: (
    projectRoot?: string,
    severity?: string,
    dimension?: string,
    limit = 500,
    offset = 0,
  ): Promise<FindingsResponse> =>
    request<FindingsResponse>(
      `/runs/findings${buildQuery(projectRoot, {
        severity,
        dimension,
        limit,
        offset,
      })}`,
    ),

  checkpoints: (projectRoot?: string): Promise<CheckpointView> =>
    request<CheckpointView>(`/checkpoints${buildQuery(projectRoot)}`),

  restoreCheckpoint: (projectRoot?: string): Promise<OperationResult> =>
    request<OperationResult>(
      `/checkpoints/restore${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "POST" },
    ),

  clearCheckpoint: (projectRoot?: string): Promise<OperationResult> =>
    request<OperationResult>(
      `/checkpoints/clear${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "POST" },
    ),

  config: (projectRoot?: string): Promise<ConfigView> =>
    request<ConfigView>(`/config${buildQuery(projectRoot)}`),

  // ``expectedVersion`` is the ``version`` returned by the GET the editor was
  // built from. When the file changed on disk in the meantime the backend
  // answers 409 instead of overwriting the other edit; the editor then reloads
  // and the operator re-applies their change.
  saveConfig: (
    config: Record<string, unknown>,
    projectRoot?: string,
    expectedVersion?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/config${buildQuery(projectRoot, { confirm: "true", expectedVersion })}`,
      {
        method: "PUT",
        body: JSON.stringify(config),
      },
    ),

  reports: (projectRoot?: string): Promise<ReportView[]> =>
    request<ReportView[]>(`/reports${buildQuery(projectRoot)}`),

  // Cross-run trend: per-run new/fixed/regressed history + stubborn/open
  // findings. Read-only; safe to refetch while a loop is running.
  trend: (
    projectRoot?: string,
    top = 20,
    history = 20,
  ): Promise<TrendReport> =>
    request<TrendReport>(
      `/trend${buildQuery(projectRoot, { top, history })}`,
    ),

  // Download the same report as a Markdown digest, CSV table or JSON payload.
  exportTrend: downloadTrendExport,

  // Recipe gate: the latest ④ answer plus every round that published one,
  // read from this project's decision log. Read-only; absence is its own state.
  gate: (projectRoot?: string, limit = 100): Promise<GateView> =>
    request<GateView>(`/gate${buildQuery(projectRoot, { limit })}`),

  // Environment self-check (the console's `ih doctor`). Deterministic,
  // network-free, read-only; each check carries an actionable remedy.
  doctor: (projectRoot?: string): Promise<DoctorView> =>
    request<DoctorView>(`/doctor${buildQuery(projectRoot)}`),

  // Capability inventory: the exact tools/commands/skills/plugins/MCP configs
  // this project loads, plus static MCP problems with copyable remedies.
  // Read-only and network-free.
  capabilities: (projectRoot?: string): Promise<CapabilitiesView> =>
    request<CapabilitiesView>(`/capabilities${buildQuery(projectRoot)}`),

  // Lifecycle hooks: what the engine registers (settings + enabled plugins)
  // and the execution trail. Read-only; no hook command is spawned and no
  // model is invoked. `success` filters the log by outcome.
  hooks: (projectRoot?: string): Promise<HooksView> =>
    request<HooksView>(`/hooks${buildQuery(projectRoot)}`),

  hookLog: (
    projectRoot?: string,
    opts: {
      event?: string;
      hookType?: string;
      success?: string;
      offset?: number;
      limit?: number;
    } = {},
  ): Promise<HookLogView> =>
    request<HookLogView>(
      `/hooks/log${buildQuery(projectRoot, {
        event: opts.event,
        hook_type: opts.hookType,
        success: opts.success,
        offset: opts.offset,
        limit: opts.limit,
      })}`,
    ),

  // Project memory bank: the persistent context the engine injects into every
  // runtime prompt. Read-only; `query` ranks matches with the engine's scorer
  // and `prompt` returns the exact injection preview for a task.
  memory: (
    projectRoot?: string,
    opts: { query?: string; prompt?: string } = {},
  ): Promise<MemoryView> =>
    request<MemoryView>(
      `/memory${buildQuery(projectRoot, { query: opts.query, prompt: opts.prompt })}`,
    ),

  // One memory file's full content (for the console's detail pane).
  memoryEntry: (name: string, projectRoot?: string): Promise<MemoryEntryDetail> =>
    request<MemoryEntryDetail>(
      `/memory/entry${buildQuery(projectRoot, { name })}`,
    ),

  // Create/overwrite a memory file. Mutating and audited; `input.overwrite`
  // is required to replace an existing file (409 otherwise).
  createMemory: (
    input: MemoryCreateInput,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/memory${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "POST", body: JSON.stringify(input) },
    ),

  // Delete a memory file and unlink it from MEMORY.md. Mutating and audited.
  deleteMemory: (
    name: string,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/memory${buildQuery(projectRoot, { name, confirm: "true" })}`,
      { method: "DELETE" },
    ),

  // Scheduled reviews: the machine-level cron registry, scheduler liveness and
  // recent executions (with bounded output tails). The GET is read-only; the
  // three mutating calls below are the console's branch of
  // `ih iterate schedule add|remove` and require the secondary-confirm flag.
  schedules: (limit = 50): Promise<SchedulesView> =>
    request<SchedulesView>(`/schedules${buildQuery(undefined, { limit })}`),

  // Register/update the canonical scheduled quick review for a project root.
  // Moving an existing job to another directory answers 409 unless
  // `input.replace` is true, so the UI can ask before retargeting it.
  upsertSchedule: (input: ScheduleUpsertInput): Promise<OperationResult> =>
    request<OperationResult>(`/schedules${buildQuery(undefined, { confirm: "true" })}`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  // Pause/resume one job without touching the others or the daemon.
  setScheduleEnabled: (
    name: string,
    enabled: boolean,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/schedules/enabled${buildQuery(projectRoot, {
        name,
        enabled: enabled ? "true" : "false",
        confirm: "true",
      })}`,
      { method: "POST" },
    ),

  // Remove one job from the machine-level registry.
  removeSchedule: (name: string, projectRoot?: string): Promise<OperationResult> =>
    request<OperationResult>(
      `/schedules${buildQuery(projectRoot, { name, confirm: "true" })}`,
      { method: "DELETE" },
    ),

  // Team roster: one row per worker the harness is tracking. Read-only, so it
  // is safe to poll while a loop is running. `tail=0` returns sizes without
  // payloads — used by the header counters so they stay cheap.
  agents: (
    projectRoot?: string,
    opts: { tail?: number; status?: string; limit?: number } = {},
  ): Promise<AgentsView> =>
    request<AgentsView>(
      `/agents${buildQuery(projectRoot, {
        tail: opts.tail,
        status: opts.status,
        limit: opts.limit,
      })}`,
    ),

  // Stop one worker. Mutating and audited: the `confirm` flag is the UI's
  // secondary-confirmation contract, not a server-side formality.
  shutdownAgent: (
    agentId: string,
    projectRoot?: string,
  ): Promise<StopAgentResult> =>
    request<StopAgentResult>(
      `/agents/shutdown${buildQuery(projectRoot, { agent_id: agentId, confirm: "true" })}`,
      { method: "POST" },
    ),

  // Compare two finished runs by finding fingerprint (new/fixed/regressed/
  // unchanged). 0 means "let the server pick": last run, and the one before it.
  runDiff: (
    projectRoot?: string,
    fromIndex = 0,
    toIndex = 0,
  ): Promise<RunDiffView> =>
    request<RunDiffView>(
      `/runs/diff${buildQuery(projectRoot, {
        from: fromIndex || undefined,
        to: toIndex || undefined,
      })}`,
    ),

  reportPreview: (name: string, projectRoot?: string): Promise<ReportPreview> =>
    request<ReportPreview>(
      `/reports/preview${buildQuery(projectRoot, { name })}`,
    ),

  // ---- Chat / human-in-the-loop (design §18) ----
  chatStart: (
    body: StartRequest,
    projectRoot?: string,
  ): Promise<{ runId: string; status: string }> =>
    request<{ runId: string; status: string }>(
      `/chat/start${buildQuery(projectRoot)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  // Chat reads are project-scoped like every other endpoint: the run manager
  // is a process singleton, so without the root the panel could show another
  // project's run while the console is pointed elsewhere, and a restarted
  // server could not read this project's persisted transcript.
  chatStatus: (projectRoot?: string): Promise<ChatRunStatus> =>
    request<ChatRunStatus>(`/chat/status${buildQuery(projectRoot)}`),

  chatHistory: (projectRoot?: string): Promise<ChatMessage[]> =>
    request<ChatMessage[]>(`/chat/history${buildQuery(projectRoot)}`),

  chatSend: (
    content: string,
  ): Promise<{ answered: boolean; waitingFor?: string; nudged?: boolean }> =>
    request<{ answered: boolean; waitingFor?: string; nudged?: boolean }>(
      `/chat/message`,
      { method: "POST", body: JSON.stringify({ content }) },
    ),

  chatControl: (
    action: "pause" | "resume" | "stop",
  ): Promise<{ ok: boolean; message: string }> =>
    request<{ ok: boolean; message: string }>(`/chat/control`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),

  // Safety hatch: cancel a live run and return to idle. Mutating, so it always
  // carries the secondary-confirm flag (and is audited server-side).
  chatReset: (projectRoot?: string): Promise<{ ok: boolean; status: string }> =>
    request<{ ok: boolean; status: string }>(
      `/chat/reset${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "POST" },
    ),

  // ---- Workspaces (design §17.3 P4) ----
  workspaces: (projectRoot?: string): Promise<WorkspaceView[]> =>
    request<WorkspaceView[]>(`/workspaces${buildQuery(projectRoot)}`),

  removeWorkspace: (
    slug: string,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/workspaces/remove${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "POST", body: JSON.stringify({ slug }) },
    ),

  // ---- Findings triage (design §17.3 P2) ----
  triageDecisions: (projectRoot?: string): Promise<TriageDecision[]> =>
    request<TriageDecision[]>(`/runs/findings/triage${buildQuery(projectRoot)}`),

  triageFinding: (
    file: string,
    line: number | null,
    dimension: string,
    decision: "approve" | "reject",
    note?: string,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/runs/findings/triage${buildQuery(projectRoot, { confirm: "true" })}`,
      {
        method: "POST",
        body: JSON.stringify({ file, line, dimension, decision, note }),
      },
    ),

  // Undo a single triage decision. Without this, a mis-click on one finding
  // was a one-way door: the only removal path wiped every decision, so the
  // operator had to either live with a wrong verdict or destroy the journal.
  dismissTriageFinding: (
    file: string,
    line: number | null,
    dimension: string,
    projectRoot?: string,
  ): Promise<OperationResult> =>
    request<OperationResult>(
      `/runs/findings/triage/dismiss${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "DELETE", body: JSON.stringify({ file, line, dimension }) },
    ),

  clearTriage: (projectRoot?: string): Promise<OperationResult> =>
    request<OperationResult>(
      `/runs/findings/triage${buildQuery(projectRoot, { confirm: "true" })}`,
      { method: "DELETE" },
    ),
};


