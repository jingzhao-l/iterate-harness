// Pure helpers for the self-check page (design §17.3 P11). Kept out of the
// component so status labeling and grouping rules are unit-testable.

import type { DoctorCheck, DoctorView } from "./types";

export type DoctorStatus = "ok" | "warn" | "fail";

export const STATUS_LABELS: Record<DoctorStatus, string> = {
  ok: "正常",
  warn: "告警",
  fail: "失败",
};

export const STATUS_ORDER: Record<DoctorStatus, number> = {
  fail: 0,
  warn: 1,
  ok: 2,
};

/** The single sentence the page leads with, mirroring the CLI's final line. */
export function doctorHeadline(report: DoctorView | null): {
  label: string;
  tone: DoctorStatus;
  detail: string;
} {
  if (!report) {
    return {
      label: "尚未检查",
      tone: "warn",
      detail: "运行自检后即可看到本机是否就绪。",
    };
  }
  const { ok: okCount, warn: warnCount, fail: failCount } = report.summary;
  if (report.has_failures) {
    return {
      label: `${failCount} 项失败`,
      tone: "fail",
      detail: `阻塞性问题 ${failCount} 项 · 告警 ${warnCount} 项 · 正常 ${okCount} 项，按下方 remedy 逐项修复后重跑。`,
    };
  }
  if (report.has_warnings) {
    return {
      label: `${warnCount} 项告警`,
      tone: "warn",
      detail: `可以运行，但建议处理 ${warnCount} 项告警（正常 ${okCount} 项）。`,
    };
  }
  return {
    label: "环境就绪",
    tone: "ok",
    detail: `${okCount} 项检查全部通过。`,
  };
}

/** Group checks by status in the order failures → warnings → ok. */
export function groupChecks(checks: DoctorCheck[]): DoctorCheck[] {
  return [...checks].sort(
    (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status],
  );
}

/** True when a check needs the operator's attention (fail or warn). */
export function needsAttention(check: DoctorCheck): boolean {
  return check.status !== "ok";
}

export function statusTone(status: DoctorStatus): string {
  if (status === "fail") return "severity-critical";
  if (status === "warn") return "amber";
  return "green";
}
