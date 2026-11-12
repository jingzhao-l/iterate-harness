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

/**
 * A copyable `ih hooks test` command tailored to one event (design §17.3 P15).
 *
 * A hook normally only fires deep inside a real run; the console can hand the
 * operator the exact command that exercises it *now*, with the payload field
 * that event's matchers read (tool name / prompt / notification type). This is
 * the console half of the "test before you run" channel.
 */
export function hookTestCommand(event: string): string {
  if (event === "pre_tool_use" || event === "post_tool_use") {
    return `ih hooks test ${event} --tool-name bash`;
  }
  if (event === "user_prompt_submit") {
    return `ih hooks test ${event} --prompt "describe the task"`;
  }
  if (event === "notification") {
    return `ih hooks test ${event} --notification-type permission_prompt`;
  }
  return `ih hooks test ${event}`;
}

export interface HookRowStatus {
  text: string;
  cls: string;
}

/** Static-validation badge for a registered hook row. */
export function hookRowStatus(issues: string[] | undefined): HookRowStatus {
  return issues && issues.length > 0
    ? { text: "问题", cls: "warn" }
    : { text: "就绪", cls: "neutral" };
}
