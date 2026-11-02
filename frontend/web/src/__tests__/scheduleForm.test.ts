// Schedule-editor form model tests (design §17.3 P10). The form's whole job
// is to let an operator register/pause/remove a scheduled review from the
// console without a terminal; these pin the local validation (the server is
// authoritative for cron/timezone semantics) and the wording of the one
// destructive case, moving a plan to another project directory.

import { describe, expect, it } from "vitest";
import {
  buildScheduleInput,
  canonicalJob,
  defaultScheduleForm,
  describeScheduleMode,
  movesExistingSchedule,
  type ScheduleFormState,
} from "../scheduleForm";
import type { CronJobView, SchedulesView } from "../types";

function form(overrides: Partial<ScheduleFormState> = {}): ScheduleFormState {
  return { ...defaultScheduleForm("/repo"), ...overrides };
}

function job(overrides: Partial<CronJobView> = {}): CronJobView {
  return {
    name: "iterate.review-changed",
    schedule: "0 9 * * 1-5",
    timezone: null,
    command: "ih iterate review --changed --clean-ok --ref HEAD --rounds 3",
    cwd: "/repo",
    enabled: true,
    schedule_valid: true,
    next_run: "2026-11-03T01:00:00+00:00",
    last_run: null,
    last_status: null,
    ...overrides,
  };
}

describe("buildScheduleInput", () => {
  it("builds a trimmed payload from a valid form", () => {
    const result = buildScheduleInput(form({ timezone: "Asia/Shanghai", rounds: "5", timeout: "1800" }));
    expect(result.errors).toEqual([]);
    expect(result.input).toEqual({
      project_root: "/repo",
      schedule: "0 9 * * 1-5",
      ref: "HEAD",
      rounds: 5,
      timeout: 1800,
      mode: "dry-run",
      timezone: "Asia/Shanghai",
    });
  });

  it("omits the timezone key when empty (UTC)", () => {
    const result = buildScheduleInput(form({ timezone: "  " }));
    expect(result.errors).toEqual([]);
    expect(result.input?.timezone).toBeUndefined();
  });

  it("rejects an empty or non-5-field cron", () => {
    expect(buildScheduleInput(form({ schedule: "" })).errors[0]).toContain("cron");
    expect(buildScheduleInput(form({ schedule: "0 9 * *" })).errors[0]).toContain("5 段");
  });

  it("rejects an unsafe git ref", () => {
    const result = buildScheduleInput(form({ ref: "--upload-pack=evil" }));
    expect(result.errors[0]).toContain("ref");
    expect(result.input).toBeNull();
  });

  it("rejects out-of-range rounds and timeout", () => {
    expect(buildScheduleInput(form({ rounds: "0" })).errors[0]).toContain("轮数");
    expect(buildScheduleInput(form({ rounds: "21" })).errors[0]).toContain("轮数");
    expect(buildScheduleInput(form({ rounds: "x" })).errors[0]).toContain("轮数");
    expect(buildScheduleInput(form({ timeout: "59" })).errors[0]).toContain("超时");
    expect(buildScheduleInput(form({ timeout: "7201" })).errors[0]).toContain("超时");
  });

  it("rejects a malformed timezone", () => {
    const result = buildScheduleInput(form({ timezone: "Not A Zone" }));
    expect(result.errors[0]).toContain("时区");
  });
});

describe("canonicalJob", () => {
  it("finds the canonical quick-review job in a view", () => {
    const view = { jobs: [job(), job({ name: "other" })] } as SchedulesView;
    expect(canonicalJob(view)?.name).toBe("iterate.review-changed");
  });

  it("returns null for an empty view", () => {
    expect(canonicalJob(null)).toBeNull();
    expect(canonicalJob({ jobs: [] } as unknown as SchedulesView)).toBeNull();
  });
});

describe("movesExistingSchedule", () => {
  it("is false when there is no existing job", () => {
    expect(movesExistingSchedule(null, "/repo")).toBe(false);
  });

  it("is false when the target equals the current directory", () => {
    expect(movesExistingSchedule(job(), "/repo")).toBe(false);
    expect(movesExistingSchedule(job(), "/repo/")).toBe(false);
  });

  it("is true when the target is a different directory", () => {
    expect(movesExistingSchedule(job(), "/other")).toBe(true);
    expect(movesExistingSchedule(job({ cwd: "/other" }), "/repo")).toBe(true);
  });

  it("is false when the target is empty (server will decide the root)", () => {
    expect(movesExistingSchedule(job(), "")).toBe(false);
  });
});

describe("describeScheduleMode", () => {
  it("states the write posture of each mode", () => {
    expect(describeScheduleMode("dry-run")).toContain("只评审");
    expect(describeScheduleMode("normal")).toContain("自动修复");
  });
});
