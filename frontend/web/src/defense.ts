// Defensive-kernel presentation helpers (design §17.3 P17). The page renders
// the effective guard, assumption outcomes and the rollback/verdict trail.
// Keeping labels, tones and copyable commands here lets the page and its tests
// agree on what each state means (and keeps an outcome word from silently
// drifting into a CSS class).

import type { DefenseEvent, DefenseView } from "./types";

export interface DefenseHeadline {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "neutral";
}

export const DEFENSE_KIND_LABELS: Record<string, string> = {
  rollback: "回滚",
  invariant: "不变量",
  "manual-check": "手动检查",
};

export const DEFENSE_OUTCOME_LABELS: Record<string, string> = {
  pass: "通过",
  fail: "失败",
  error: "无法评估",
  unmeasured: "未测量",
  failed_tool: "工具失败",
};

export const ASSUMPTION_STATUS_LABELS: Record<string, string> = {
  declared: "已声明",
  holds: "成立",
  falsified: "已证伪",
};

/** Badge tone for one journal outcome (existing badge palette). */
export function outcomeTone(outcome: string, rolledBack = false): string {
  // A rollback is the strongest signal on the page: it must never render as a
  // green "pass" just because the outcome word says pass.
  if (rolledBack || outcome === "fail" || outcome === "error") return "severity-high";
  if (outcome === "pass") return "green";
  if (outcome === "unmeasured") return "amber";
  if (outcome === "failed_tool") return "amber";
  return "neutral";
}

/** Human label for one journal outcome (unknown values stay visible). */
export function outcomeLabel(outcome: string): string {
  return DEFENSE_OUTCOME_LABELS[outcome] ?? (outcome || "?");
}

/** Human label for one journal kind. */
export function kindLabel(kind: string): string {
  return DEFENSE_KIND_LABELS[kind] ?? (kind || "?");
}

/** Human label for an assumption status. */
export function assumptionStatusLabel(status: string): string {
  return ASSUMPTION_STATUS_LABELS[status] ?? (status || "?");
}

/** Badge tone for an assumption status. */
export function assumptionTone(status: string): string {
  if (status === "falsified") return "severity-high";
  if (status === "declared") return "amber";
  if (status === "holds") return "green";
  return "neutral";
}

/** The one-line result for a journal row. */
export function eventResultLabel(event: DefenseEvent): string {
  if (event.rolled_back) return `已回滚 · ${outcomeLabel(event.outcome)}`;
  return outcomeLabel(event.outcome);
}

/**
 * Headline: configuration problems and a disabled/unmeasured guard outrank a
 * stale "pass", because they are the conditions that decide whether the guard
 * protects anything at all.
 */
export function defenseHeadline(view: DefenseView | null): DefenseHeadline {
  if (!view) {
    return { label: "读取中", detail: "正在解析防御守卫…", tone: "neutral" };
  }
  if (view.has_problems) {
    return {
      label: `${view.problems.length} 项配置问题`,
      detail: "有检查永远不会通过，代码模式编辑会被持续回滚；先按修复方法处理。",
      tone: "warn",
    };
  }
  if (!view.policy.enabled) {
    return {
      label: view.policy.source === "disabled" ? "守卫已显式关闭" : "未配置守卫",
      detail:
        view.policy.source === "disabled"
          ? "invariants 段为空：编辑不会被事后校验。"
          : "没有可运行的不变式检查：编辑不会被事后校验或回滚。",
      tone: "warn",
    };
  }
  const lastVerdict = view.summary.last_verdict;
  if (lastVerdict && lastVerdict !== "pass") {
    return {
      label: `最近判定：${outcomeLabel(lastVerdict)}`,
      detail: `守卫配置了 ${view.policy.checks} 个检查；最近一次没有通过，历史见回滚台账。`,
      tone: "warn",
    };
  }
  return {
    label: `${view.policy.checks} 个检查就绪`,
    detail: `${view.policy.source_label} · 编辑后自动校验，失败即回滚。`,
    tone: "ok",
  };
}

function shellQuote(value: string): string {
  if (!value) return "";
  if (/^[A-Za-z0-9_./:=@%+-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The exact command that runs the same guard in a terminal. The page never
 * executes invariant commands itself; it copies this command so the operator
 * (or CI) runs them with the engine's own executor.
 */
export function defenseCheckCommand(projectRoot: string): string {
  const root = projectRoot.trim();
  return root ? `ih defense check --project-root ${shellQuote(root)}` : "ih defense check";
}

/** A copyable export command for the current journal filters. */
export function defenseJournalCommand(
  projectRoot: string,
  filters: { kind?: string; outcome?: string; tool?: string } = {},
): string {
  const parts = ["ih", "defense", "journal"];
  const root = projectRoot.trim();
  if (root) parts.push("--project-root", shellQuote(root));
  if (filters.kind) parts.push("--kind", shellQuote(filters.kind));
  if (filters.outcome) parts.push("--outcome", shellQuote(filters.outcome));
  if (filters.tool) parts.push("--tool", shellQuote(filters.tool));
  parts.push("--format", "markdown");
  return parts.join(" ");
}

/** Short local timestamp: "MM-DD HH:MM:SS" (keeps the date for old events). */
export function shortTimestamp(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}
