// Scheduled-reviews page helpers (design §17.3 P10). The two answers this page
// exists to give — "will it run?" and "did the last one pass?" — are pinned
// here, including the stopped-scheduler wording that must not read as healthy.

import { describe, expect, it } from "vitest";
import {
  durationLabel,
  historyNewestFirst,
  historyTone,
  jobNeedsAttention,
  nextRunLabel,
  schedulerHeadline,
} from "../schedules";
import type { CronHistoryView, CronJobView, SchedulerStatus } from "../types";

function status(overrides: Partial<SchedulerStatus> = {}): SchedulerStatus {
  return {
    running: false,
    pid: null,
    total_jobs: 0,
    enabled_jobs: 0,
    log_file: "/tmp/logs/cron_scheduler.log",
    history_file: "/tmp/data/cron_history.jsonl",
    ...overrides,
  };
}

function job(overrides: Partial<CronJobView> = {}): CronJobView {
  return {
    name: "iterate-quick-review",
    schedule: "0 9 * * *",
    timezone: "Asia/Shanghai",
    command: "ih iterate run --changed",
    cwd: "/tmp/project",
    enabled: true,
    schedule_valid: true,
    next_run: "2026-10-30T01:00:00+00:00",
    last_run: null,
    last_status: null,
    ...overrides,
  };
}

function entry(overrides: Partial<CronHistoryView> = {}): CronHistoryView {
  return {
    name: "job",
    command: "ih iterate run",
    started_at: "2026-10-29T01:00:00+00:00",
    ended_at: "2026-10-29T01:00:05+00:00",
    status: "success",
    returncode: 0,
    duration_s: 5,
    stdout_tail: "",
    stderr_tail: "",
    ...overrides,
  };
}

describe("schedulerHeadline", () => {
  it("states a stopped scheduler as stopped, not idle", () => {
    const headline = schedulerHeadline(status({ running: false }));
    expect(headline.label).toBe("未运行");
    expect(headline.tone).toBe("neutral");
    expect(headline.detail).toContain("不会自动执行");
  });

  it("names the pid when it is running", () => {
    const headline = schedulerHeadline(status({ running: true, pid: 4242 }));
    expect(headline.label).toBe("运行中");
    expect(headline.tone).toBe("good");
    expect(headline.detail).toContain("4242");
  });

  it("does not guess when no status was read", () => {
    expect(schedulerHeadline(null).label).toBe("未知");
  });
});

describe("nextRunLabel", () => {
  const now = new Date("2026-10-29T12:00:00Z");

  it("renders minutes, hours and days", () => {
    expect(nextRunLabel("2026-10-29T12:05:00Z", now)).toBe("5 分钟后");
    expect(nextRunLabel("2026-10-29T14:30:00Z", now)).toBe("2 小时 30 分后");
    expect(nextRunLabel("2026-10-31T15:00:00Z", now)).toBe("2 天 3 小时后");
  });

  it("says 已到期 when the computed time is already past", () => {
    expect(nextRunLabel("2026-10-29T11:59:00Z", now)).toBe("已到期");
  });

  it("treats missing or invalid times as absent", () => {
    expect(nextRunLabel(null, now)).toBe("—");
    expect(nextRunLabel("not-a-date", now)).toBe("—");
  });
});

describe("durationLabel", () => {
  it("keeps sub-minute precision and expands minutes", () => {
    expect(durationLabel(12.5)).toBe("12.5 秒");
    expect(durationLabel(65)).toBe("1 分 5 秒");
    expect(durationLabel(null)).toBe("—");
  });
});

describe("historyTone", () => {
  it("separates success from failed/error", () => {
    expect(historyTone("success")).toBe("good");
    expect(historyTone("failed")).toBe("bad");
    expect(historyTone("error")).toBe("bad");
    expect(historyTone("running")).toBe("neutral");
  });
});

describe("historyNewestFirst", () => {
  it("reverses a copy without mutating the API order", () => {
    const entries = [entry({ name: "a" }), entry({ name: "b" })];
    expect(historyNewestFirst(entries).map((item) => item.name)).toEqual(["b", "a"]);
    expect(entries.map((item) => item.name)).toEqual(["a", "b"]);
  });
});

describe("jobNeedsAttention", () => {
  it("flags jobs that cannot fire or whose last run failed", () => {
    expect(jobNeedsAttention(job())).toBe(false);
    expect(jobNeedsAttention(job({ enabled: false }))).toBe(true);
    expect(jobNeedsAttention(job({ schedule_valid: false }))).toBe(true);
    expect(jobNeedsAttention(job({ last_status: "failed" }))).toBe(true);
    expect(jobNeedsAttention(job({ last_status: "success" }))).toBe(false);
  });
});
