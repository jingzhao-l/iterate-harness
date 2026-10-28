// Pure helpers for the recipe-gate page (design §17.3 P9). Kept out of the
// component so the headline rules and digest shortening are unit-testable.

import type { GateRecord } from "./types";

export type GateTone = "good" | "bad" | "neutral";

export interface GateHeadline {
  label: string;
  tone: GateTone;
  detail: string;
}

/**
 * The one sentence the page leads with. Absence has its own label on purpose:
 * a project that never asked must not look like a project that passed, which is
 * the same rule the CLI reader follows.
 */
export function gateHeadline(record: GateRecord | null | undefined): GateHeadline {
  if (!record) {
    return {
      label: "尚未记录",
      tone: "neutral",
      detail: "没有记录不等于通过：先跑一轮测量过不变量的循环。",
    };
  }
  if (record.allowed) {
    return {
      label: "允许进入复验",
      tone: "good",
      detail: `第 ${record.round} 轮 · 不变量判定 ${record.invariant_verdict}`,
    };
  }
  return {
    label: "已拒绝",
    tone: "bad",
    detail: record.refusal?.code
      ? `第 ${record.round} 轮 · ${record.refusal.code}`
      : `第 ${record.round} 轮 · 不变量判定 ${record.invariant_verdict}`,
  };
}

/** Newest-first copy for the history table (the API returns oldest first). */
export function newestFirst(records: GateRecord[]): GateRecord[] {
  return [...records].reverse();
}

/** Stable list key: a round number alone repeats across resumed runs. */
export function recordKey(record: GateRecord): string {
  return `${record.round}@${record.timestamp}`;
}

/** ``sha256:<64 hex>`` → ``sha256:<12 hex>…``; null stays an explicit dash. */
export function shortDigest(digest: string | null): string {
  if (!digest) return "—";
  const separator = digest.indexOf(":");
  const prefix = separator >= 0 ? digest.slice(0, separator + 1) : "";
  const value = separator >= 0 ? digest.slice(separator + 1) : digest;
  if (!value) return digest;
  return `${prefix}${value.slice(0, 12)}${value.length > 12 ? "…" : ""}`;
}
