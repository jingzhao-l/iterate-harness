// Permission policy helpers (design §17.3 P16). The page renders the merged
// policy the engine enforces and lets the operator simulate a call before a
// run. These pure functions own the labels and the one-click scenarios so the
// page and its tests agree on what a verdict means.

import type {
  PermissionCheckInput,
  PermissionPathRule,
  PermissionVerdict,
  PermissionsView,
} from "./types";

export interface PermissionHeadline {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "neutral";
}

export const MODE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "当前配置" },
  { value: "default", label: "默认（改动需确认）" },
  { value: "plan", label: "计划（禁止改动）" },
  { value: "full_auto", label: "全自动（不提示）" },
];

export const VERDICT_LABELS: Record<PermissionVerdict, string> = {
  allowed: "允许",
  confirm: "需确认",
  denied: "拒绝",
};

/** Badge tone for a simulated verdict (existing CSS badge palette). */
export function verdictTone(verdict: PermissionVerdict): string {
  if (verdict === "allowed") return "green";
  if (verdict === "confirm") return "amber";
  return "severity-high";
}

/** Human label for how the simulator classified read-only-ness. */
export function readOnlySourceLabel(source: string): string {
  if (source === "explicit") return "显式指定";
  if (source === "inferred") return "按工具推断";
  return "未知工具（按可写处理）";
}

/** "拒绝" / "允许" for one effective path rule. */
export function pathRuleLabel(rule: PermissionPathRule): string {
  return rule.allow ? "允许" : "拒绝";
}

/**
 * Headline: configuration problems outrank the mode, because a rule that can
 * never take effect is the condition the operator must fix first. A
 * ``full_auto`` mode is a warning, not a success — it is a deliberate risk.
 */
export function permissionHeadline(
  view: PermissionsView | null,
): PermissionHeadline {
  if (!view) {
    return { label: "读取中", detail: "正在加载权限策略…", tone: "neutral" };
  }
  if (view.has_problems) {
    return {
      label: `${view.problems.length} 项配置问题`,
      detail: "有规则永远不会生效，先按修复方法处理再开跑。",
      tone: "warn",
    };
  }
  return {
    label: `${view.mode_label || view.mode}模式`,
    detail: view.mode_detail || "策略已加载。",
    tone: view.mode === "full_auto" ? "warn" : "ok",
  };
}

export interface PermissionPreset {
  id: string;
  label: string;
  description: string;
  input: Omit<PermissionCheckInput, "projectRoot">;
}

/**
 * One-click scenarios. Each exercises a different boundary so the page can be
 * understood without knowing the config schema: a built-in sensitive path, a
 * plain write, a risk-area write, a shell command and a read-only call.
 */
export const PERMISSION_PRESETS: PermissionPreset[] = [
  {
    id: "sensitive-read",
    label: "读取 .env",
    description: "内置敏感路径边界：无论什么模式都拒绝",
    input: { tool: "read_file", path: ".env" },
  },
  {
    id: "source-write",
    label: "改写源码",
    description: "默认模式需确认；全自动放行",
    input: {
      tool: "write_file",
      path: "src/app.py",
      content: "print('hello')",
    },
  },
  {
    id: "risk-area-write",
    label: "写入风险区",
    description: "risk_areas 下的改动始终需确认（若已配置）",
    input: {
      tool: "write_file",
      path: "db/migrations/001.sql",
      content: "ALTER TABLE users ADD COLUMN nickname text;",
    },
  },
  {
    id: "shell",
    label: "执行 shell",
    description: "命令拒绝规则与 bash 路径提取",
    input: { tool: "bash", command: "git push --force origin main" },
  },
  {
    id: "read-only",
    label: "读取 README",
    description: "只读工具在任何模式都放行",
    input: { tool: "read_file", path: "README.md" },
  },
];

/** Look up a preset by id (unknown ids return ``null`` rather than throwing). */
export function permissionPreset(id: string): PermissionPreset | null {
  return PERMISSION_PRESETS.find((preset) => preset.id === id) ?? null;
}

function shellQuote(value: string): string {
  if (!value) return "";
  if (/^[A-Za-z0-9_./:=@%+-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * The exact `ih permissions check …` call equivalent to a simulator input.
 * Copyable from the page so a browser finding can be re-run in a terminal or
 * pasted into a CI step without translating fields by hand.
 */
export function permissionCheckCommand(
  input: Omit<PermissionCheckInput, "projectRoot">,
): string {
  const parts = ["ih", "permissions", "check", shellQuote(input.tool)];
  if (input.path) parts.push("--path", shellQuote(input.path));
  if (input.command) parts.push("--command", shellQuote(input.command));
  if (input.content) parts.push("--content", shellQuote(input.content));
  if (input.mode) parts.push("--mode", shellQuote(input.mode));
  if (input.readOnly === true) parts.push("--read-only");
  if (input.readOnly === false) parts.push("--mutating");
  return parts.join(" ");
}
