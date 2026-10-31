// Pure helpers for the two-run comparison page (design §17.3 P2). The page's
// job is to answer "what changed between these two runs?", so the section
// wording and the pair normalisation are unit-tested rather than inlined.

import type { RunDiffCounts, RunDiffView, RunSnapshotView } from "./types";

export type DiffSectionKey = "new" | "fixed" | "regressed" | "unchanged";

export interface DiffSectionSpec {
  key: DiffSectionKey;
  label: string;
  hint: string;
  tone: string;
}

export const DIFF_SECTIONS: DiffSectionSpec[] = [
  { key: "new", label: "新增", hint: "只在较晚一次运行里出现", tone: "severity-high" },
  { key: "fixed", label: "已修复", hint: "较早一次有、较晚一次没有", tone: "green" },
  { key: "regressed", label: "回归", hint: "此前修过、这次又出现", tone: "severity-critical" },
  { key: "unchanged", label: "未变", hint: "两次运行都在", tone: "gray" },
];

/** One option label: index, local time, verdict and finding count. */
export function runOptionLabel(run: RunSnapshotView): string {
  const stamp = run.timestamp ? run.timestamp.slice(5, 16).replace("T", " ") : "?";
  return `#${run.index} · ${stamp} · ${run.verdict} · ${run.total} 条`;
}

/**
 * Keep the pair ordered (earlier < later) and inside the available range.
 * Selecting the same run on both sides would make "new" and "fixed" mean
 * nothing, so the other side moves one step instead of silently comparing a
 * run with itself.
 */
export function normalizePair(
  from: number,
  to: number,
  total: number,
): { from: number; to: number } {
  if (total < 2) return { from, to };
  let earlier = Math.min(Math.max(1, from), total);
  let later = Math.min(Math.max(1, to), total);
  if (earlier >= later) {
    later = Math.min(total, earlier + 1);
    earlier = later - 1;
  }
  return { from: earlier, to: later };
}

export function hasPair(view: RunDiffView | null): boolean {
  return Boolean(view && view.from_run && view.to_run);
}

/** Compact line for the cards: 新增 1 · 修复 2 · 回归 0 · 未变 3. */
export function diffSummary(counts: RunDiffCounts): string {
  return `新增 ${counts.new} · 修复 ${counts.fixed} · 回归 ${counts.regressed} · 未变 ${counts.unchanged}`;
}
