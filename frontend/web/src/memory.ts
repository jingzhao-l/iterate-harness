// Memory-bank display helpers (design §17.3 P14).
//
// The console's memory page projects the engine's memory inventory. These
// helpers keep the presentation rules (byte formatting, the server's slug
// rule, the "which rows to show" decision, and the headline) testable without
// a DOM, and pinned to the same slug rule the backend uses so the console's
// conflict test cannot disagree with the server.

import type { MemoryView } from "./types";

/** Format a byte count the way the console does (B / KB / MB). */
export function humanBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** The file-name slug the backend derives from a memory title. */
export function memorySlug(title: string): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return `${slug || "memory"}.md`;
}

/** Short, console-friendly modification time. */
export function formatModified(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(0, 16).replace("T", " ");
}

/** Which rows the table shows: ranked matches while a query is active. */
export function visibleEntries(view: MemoryView): MemoryView["entries"] {
  if (view.query) {
    return view.matches.length > 0 ? view.matches : [];
  }
  return view.entries;
}

/** The headline card: what the operator must know first. */
export function memoryHeadline(view: MemoryView): {
  label: string;
  tone: "ok" | "warn";
  detail: string;
} {
  if (!view.exists && view.entries.length === 0) {
    return {
      label: "尚未创建",
      tone: "warn",
      detail: "记忆目录还不存在；第一条记忆会自动创建它",
    };
  }
  if (view.problems.length > 0) {
    return {
      label: `需处理 ${view.problems.length} 项`,
      tone: "warn",
      detail: "有记忆不可读或索引断链，引擎会在提示词里读到",
    };
  }
  return {
    label: "就绪",
    tone: "ok",
    detail: `${view.entries.length} 条记忆 · ${humanBytes(Number(view.summary.bytes ?? 0))}`,
  };
}
