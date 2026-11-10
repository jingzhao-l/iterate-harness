// Lifecycle-hook display helpers (design §17.3 P15). The status label, the
// type badge class, the headline cards and the "which event groups to show"
// decision are pinned here so the console's conflict test agrees with the
// backend's `HookLogEntry.success/blocked` semantics, and the registered /
// execution states do not collapse into one "no data" message.

import type { HookLogEntry, HooksView } from "./types";

export interface StatusLabel {
  text: string;
  cls: string;
}

/** Human outcome for one hook execution (mirrors the journal's flags). */
export function statusLabel(entry: HookLogEntry): StatusLabel {
  if (entry.success) return { text: "通过", cls: "neutral" };
  if (entry.blocked) return { text: "拦截", cls: "warn" };
  return { text: "失败", cls: "danger" };
}

/** CSS badge class for a hook type (command / prompt / http / agent). */
export function hookTypeClass(type: string): string {
  const safe = type || "unknown";
  if (safe === "command" || safe === "agent") return "hook-type-command";
  if (safe === "prompt") return "hook-type-prompt";
  return "hook-type-http";
}

export interface HookHeadline {
  total: number;
  events: number;
  executed: number;
  failures: number;
  blocked: number;
}

/** The four headline cards for the registered tab. */
export function hookHeadline(view: HooksView): HookHeadline {
  const summary = view.summary ?? {};
  return {
    total: Number(summary.total ?? 0),
    events: Number(summary.events ?? 0),
    executed: Number(summary.journal_total ?? 0),
    failures: Number(summary.journal_failures ?? 0),
    blocked: Number(summary.journal_blocked ?? 0),
  };
}

/** Human label for a lifecycle event (used in the log filters). */
export function eventLabel(event: string): string {
  return event.replace(/_/g, " ");
}
