// Pure helpers for the scheduled-reviews page (design §17.3 P10). The page's
// job is to answer "will it run, and did the last one pass?", so the wording
// of those two answers is unit-tested rather than inlined in the component.

import type { CronJobView, CronHistoryView, SchedulerStatus } from "./types";

export type ScheduleTone = "good" | "bad" | "neutral";

export interface ScheduleHeadline {
  label: string;
  tone: ScheduleTone;
  detail: string;
}

/** Scheduler liveness, stated so "stopped" never reads like "idle". */
export function schedulerHeadline(status: SchedulerStatus | null): ScheduleHeadline {
  if (!status) {
    return { label: "未知", tone: "neutral", detail: "还没有读到调度器状态。" };
  }
  if (status.running) {
    return {
      label: "运行中",
      tone: "good",
      detail: status.pid ? `pid ${status.pid}` : "已读取到 pid 凭证",
    };
  }
  return {
    label: "未运行",
    tone: "neutral",
    detail: "调度器未启动：到点的计划不会自动执行。",
  };
}

/**
 * Human next-run distance. The backend computes this freshly on every read, so
 * a value in the past means "the scheduler should have fired already" rather
 * than a stale registry field.
 */
export function nextRunLabel(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "—";
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return "—";
  const seconds = Math.floor((target.getTime() - now.getTime()) / 1000);
  if (seconds <= 0) return "已到期";
  if (seconds < 60) return "不到 1 分钟后";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟后`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest > 0 ? `${hours} 小时 ${rest} 分后` : `${hours} 小时后`;
  }
  const days = Math.floor(hours / 24);
  const rest = hours % 24;
  return rest > 0 ? `${days} 天 ${rest} 小时后` : `${days} 天后`;
}

export function durationLabel(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} 分 ${whole % 60} 秒`;
}

export function historyTone(status: string): ScheduleTone {
  if (status === "success") return "good";
  if (status === "failed" || status === "error") return "bad";
  return "neutral";
}

/** Newest-first copy for the history table (the API returns oldest first). */
export function historyNewestFirst(entries: CronHistoryView[]): CronHistoryView[] {
  return [...entries].reverse();
}

/**
 * A job needs attention when it cannot fire (disabled / invalid cron) or its
 * last execution failed. An enabled job that never ran is not a problem yet.
 */
export function jobNeedsAttention(job: CronJobView): boolean {
  if (!job.enabled || !job.schedule_valid) return true;
  return job.last_status === "failed" || job.last_status === "error";
}
