// Pure helpers for the trend page (design §17.3 P8). Kept out of the component
// so the aggregation rules are unit-testable without a DOM.

import type { TrendReport, TrendRun } from "./types";

export interface TrendWindowTotals {
  runs: number;
  new: number;
  fixed: number;
  regressed: number;
}

/** Sum the visible history window (not the whole project history). */
export function windowTotals(runs: TrendRun[]): TrendWindowTotals {
  return runs.reduce<TrendWindowTotals>(
    (totals, run) => ({
      runs: totals.runs + 1,
      new: totals.new + run.new,
      fixed: totals.fixed + run.fixed,
      regressed: totals.regressed + run.regressed,
    }),
    { runs: 0, new: 0, fixed: 0, regressed: 0 },
  );
}

/**
 * Net direction of the visible window: positive means more findings were
 * fixed than introduced, negative means the backlog grew. Regressions count
 * against the direction because a returning finding is a new problem again.
 */
export function netDirection(runs: TrendRun[]): number {
  const totals = windowTotals(runs);
  return totals.fixed - totals.new - totals.regressed;
}

export function directionLabel(value: number): string {
  if (value > 0) return `净改善 ${value}`;
  if (value < 0) return `净恶化 ${Math.abs(value)}`;
  return "持平";
}

export function directionTone(value: number): "good" | "bad" | "neutral" {
  if (value > 0) return "good";
  if (value < 0) return "bad";
  return "neutral";
}

/** Newest-first copy for tables (the API returns chronological order). */
export function newestFirst(runs: TrendRun[]): TrendRun[] {
  return [...runs].reverse();
}

/** True when the report has run history or library records worth showing. */
export function reportHasData(report: TrendReport | null): boolean {
  return Boolean(report && (report.has_data || report.runs.length > 0 || report.tracked > 0));
}

export type TrendExportFormat = "md" | "csv" | "json";

/**
 * Client-side fallback name for a trend export. The server normally supplies
 * the filename (stamped with the report's generated_at), but a proxy that
 * strips Content-Disposition must not leave the download unnamed.
 */
export function trendExportFilename(
  format: TrendExportFormat,
  at: Date = new Date(),
): string {
  const pad = (value: number): string => String(value).padStart(2, "0");
  const stamp =
    `${at.getUTCFullYear()}${pad(at.getUTCMonth() + 1)}${pad(at.getUTCDate())}` +
    `-${pad(at.getUTCHours())}${pad(at.getUTCMinutes())}${pad(at.getUTCSeconds())}`;
  return `iterate-trend-${stamp}.${format}`;
}
