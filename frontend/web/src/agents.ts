// Pure helpers for the team-roster page (design §17.3 P9). Kept out of the
// component so the aggregation and formatting rules are unit-testable without a DOM.

import type { AgentRow } from "./types";

export type StatusFilter = "all" | "running" | "failed" | "killed" | "completed";

export interface RosterKpis {
  running: number;
  failed: number;
  killed: number;
  completed: number;
  total: number;
}

/** The counters the page header shows, recomputed from the visible rows. */
export function rosterKpis(agents: AgentRow[]): RosterKpis {
  return agents.reduce<RosterKpis>(
    (acc, agent) => {
      if (agent.running) acc.running += 1;
      if (agent.status === "failed") acc.failed += 1;
      else if (agent.status === "killed") acc.killed += 1;
      else if (agent.status === "completed") acc.completed += 1;
      acc.total += 1;
      return acc;
    },
    { running: 0, failed: 0, killed: 0, completed: 0, total: 0 },
  );
}

/** Human elapsed time; sub-second stays 刚刚 so a fresh spawn does not read as a bug. */
export function formatDuration(seconds: number | null | undefined): string {
  const value = typeof seconds === "number" && seconds > 0 ? seconds : 0;
  if (value < 1) return "刚刚";
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value - minutes * 60);
  if (minutes < 60) return rest > 0 ? `${minutes}m${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return hours > 24
    ? `${Math.floor(hours / 24)}d${hours % 24}h`
    : `${hours}h${minutes % 60}m`;
}

/** Output size in the unit an operator actually thinks in. */
export function formatBytes(bytes: number | null | undefined): string {
  // Coerce *before* testing finiteness: `bytes ?? 0` yields a number, but
  // casting `undefined` through `as number` keeps it undefined and every
  // comparison below silently becomes false, so a missing size rendered as
  // "NaNMB" instead of "0B".
  const raw = bytes ?? 0;
  const value = Number.isFinite(raw) ? raw : 0;
  if (value <= 0) return "0B";
  if (value < 1024) return `${value}B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)}KB`;
  return `${(value / (1024 * 1024)).toFixed(1)}MB`;
}

const STATUS_LABELS: Record<string, string> = {
  running: "在跑",
  pending: "排队",
  completed: "完成",
  failed: "失败",
  killed: "已停",
  unknown: "未知",
};

const KIND_LABELS: Record<string, string> = {
  in_process: "进程内",
  subprocess: "子进程",
  shell: "Shell",
  other: "其他",
};

/** Short Chinese label for a status value; unknown values degrade to 未知. */
export function statusLabel(status: string | null | undefined): string {
  return STATUS_LABELS[status ?? ""] ?? "未知";
}

/** Short Chinese label for a worker kind; unknown kinds keep their raw name. */
export function kindLabel(kind: string | null | undefined): string {
  const raw = kind ?? "";
  return KIND_LABELS[raw] ?? (raw || "其他");
}

/** What to put in the row's primary column: the swarm identity when there is
 *  one, otherwise the task id (a plain shell task has no swarm identity). */
export function primaryId(agent: AgentRow): string {
  return agent.agent_id && agent.agent_id.trim() ? agent.agent_id.trim() : agent.id;
}

/** Filter by status, then by a case-insensitive substring over the useful fields. */
export function filterAgents(
  agents: AgentRow[],
  opts: { status?: StatusFilter; query?: string },
): AgentRow[] {
  const status = opts.status ?? "all";
  const needle = (opts.query ?? "").trim().toLowerCase();
  return agents.filter((agent) => {
    if (status !== "all") {
      // "running" is a state, not a status literal, so it is matched on its
      // own flag rather than on agent.status.
      if (status === "running") {
        if (!agent.running) return false;
      } else if (agent.status !== status) {
        return false;
      }
    }
    if (!needle) return true;
    return [
      agent.id,
      agent.agent_id,
      agent.team,
      agent.description,
      agent.status_note,
      agent.task_type,
      agent.cwd,
      agent.progress ?? "",
    ]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
}

/** Last line of a tail that carries something — that is the row's headline. */
export function lastLine(text: string | null | undefined): string {
  const body = (text ?? "").trim();
  if (!body) return "";
  const lines = body.split(/\r?\n/);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const trimmed = lines[index].trim();
    if (trimmed) return trimmed;
  }
  return "";
}

export const STATUS_FILTERS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "running", label: "在跑" },
  { value: "failed", label: "失败" },
  { value: "killed", label: "已停" },
  { value: "completed", label: "完成" },
];
