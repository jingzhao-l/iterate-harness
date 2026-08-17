// Thin fetch wrapper over the /api/v1 backend (design §17.5).
// No UI framework, no axios — plain fetch with JSON handling + error decode.

import type {
  ChatMessage,
  ChatRunStatus,
  CheckpointView,
  ConfigView,
  FindingsResponse,
  OperationResult,
  ReportPreview,
  ReportView,
  RunSummary,
  StartRequest,
  StatusResponse,
  TimelineEntry,
} from "./types";

const API_BASE = "/api/v1";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    let detail: unknown = undefined;
    try {
      const body = (await response.json()) as { message?: string; detail?: unknown };
      if (body.message) message = body.message;
      detail = body.detail;
    } catch {
      // non-JSON error body; keep default message
    }
    throw new ApiError(response.status, message, detail);
  }
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

export const api = {
  status: (projectRoot?: string): Promise<StatusResponse> =>
    request<StatusResponse>(`/status${buildQuery(projectRoot)}`),

  health: (projectRoot?: string): Promise<{ status: string }> =>
    request<{ status: string }>(`/health${buildQuery(projectRoot)}`),

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
  ): Promise<FindingsResponse> =>
    request<FindingsResponse>(
      `/runs/findings${buildQuery(projectRoot, {
        severity,
        dimension,
        limit,
      })}`,
    ),

  latestReport: (projectRoot?: string): Promise<Record<string, unknown>> =>
    request<Record<string, unknown>>(`/runs/report${buildQuery(projectRoot)}`),

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

  saveConfig: (config: Record<string, unknown>, projectRoot?: string): Promise<OperationResult> =>
    request<OperationResult>(`/config${buildQuery(projectRoot, { confirm: "true" })}`, {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  providers: (projectRoot?: string): Promise<{ active: string; profiles: Record<string, unknown> }> =>
    request<{ active: string; profiles: Record<string, unknown> }>(
      `/config/providers${buildQuery(projectRoot)}`,
    ),

  reports: (projectRoot?: string): Promise<ReportView[]> =>
    request<ReportView[]>(`/reports${buildQuery(projectRoot)}`),

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

  chatStatus: (): Promise<ChatRunStatus> =>
    request<ChatRunStatus>(`/chat/status`),

  chatHistory: (): Promise<ChatMessage[]> =>
    request<ChatMessage[]>(`/chat/history`),

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
};

export { API_BASE };
