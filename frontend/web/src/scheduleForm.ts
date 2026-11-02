// Form model for registering/updating a scheduled review from the console
// (design §17.3 P10). The server is authoritative for cron/timezone/ref
// semantics; this module only catches the mistakes a form can see locally so
// the operator gets an inline message instead of a round trip. It also owns
// the wording for the one destructive case the form has: moving an existing
// plan to a different project directory requires an explicit confirm.

import type { CronJobView, ScheduleUpsertInput, SchedulesView } from "./types";

export const CANONICAL_JOB_NAME = "iterate.review-changed";

export interface ScheduleFormState {
  projectRoot: string;
  schedule: string;
  timezone: string;
  ref: string;
  rounds: string;
  timeout: string;
  mode: "dry-run" | "normal";
}

export interface ScheduleFormResult {
  errors: string[];
  input: ScheduleUpsertInput | null;
}

/** Defaults mirror `ih iterate schedule add` (daily 09:00 UTC, 3 rounds). */
export function defaultScheduleForm(projectRoot = ""): ScheduleFormState {
  return {
    projectRoot,
    schedule: "0 9 * * 1-5",
    timezone: "",
    ref: "HEAD",
    rounds: "3",
    timeout: "3600",
    mode: "dry-run",
  };
}

// Git refs the engine's `validate_ref` accepts: a leading alphanumeric/_/.
// followed by alphanumerics and `_ . / ~ ^ -` (HEAD~3, origin/main, …).
const REF_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_./~^-]*$/;

function integerInRange(raw: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const value = Number(raw.trim());
  if (!Number.isSafeInteger(value) || value < min || value > max) return null;
  return value;
}

/**
 * Validate the form and produce the request body. Every error is a sentence
 * the operator can act on; no field is silently dropped or coerced.
 */
export function buildScheduleInput(form: ScheduleFormState): ScheduleFormResult {
  const errors: string[] = [];
  const schedule = form.schedule.trim();
  if (!schedule) {
    errors.push("cron 表达式不能为空（例如 0 9 * * 1-5）");
  } else if (schedule.split(/\s+/).length !== 5) {
    errors.push("cron 表达式必须是 5 段（分 时 日 月 周），例如 0 9 * * 1-5");
  }

  const ref = form.ref.trim();
  if (!ref) {
    errors.push("git ref 不能为空（默认 HEAD）");
  } else if (!REF_PATTERN.test(ref)) {
    errors.push("git ref 含非法字符（可用字母数字与 _ . / ~ ^ -，例如 HEAD~3）");
  }

  const rounds = integerInRange(form.rounds, 1, 20);
  if (rounds === null) errors.push("轮数必须是 1–20 的整数");

  const timeout = integerInRange(form.timeout, 60, 7200);
  if (timeout === null) errors.push("超时必须是 60–7200 秒的整数");

  const timezone = form.timezone.trim();
  if (timezone && !/^[A-Za-z][A-Za-z0-9_+/-]*$/.test(timezone)) {
    errors.push("时区格式不合法（IANA 名称，例如 Asia/Shanghai；留空表示 UTC）");
  }

  if (errors.length > 0) return { errors, input: null };
  return {
    errors,
    input: {
      project_root: form.projectRoot.trim() || undefined,
      schedule,
      ref,
      rounds: rounds as number,
      timeout: timeout as number,
      mode: form.mode,
      ...(timezone ? { timezone } : {}),
    },
  };
}

/** The canonical quick-review job, if this machine has one registered. */
export function canonicalJob(view: SchedulesView | null): CronJobView | null {
  if (!view) return null;
  return view.jobs.find((job) => job.name === CANONICAL_JOB_NAME) ?? null;
}

/**
 * True when saving would move the canonical job to another directory. Used to
 * pre-empt the server's 409 with the same fact in the confirmation dialog.
 * Paths are compared literally: the browser cannot resolve symlinks, and a
 * false positive only costs one extra confirmation (the server still decides).
 */
export function movesExistingSchedule(existing: CronJobView | null, target: string): boolean {
  if (!existing) return false;
  const current = existing.cwd.trim().replace(/\/+$/, "");
  const next = target.trim().replace(/\/+$/, "");
  if (!current || !next) return false;
  return current !== next;
}

/** One-line summary of what a plan will do, shown above the save button. */
export function describeScheduleMode(mode: "dry-run" | "normal"): string {
  return mode === "normal"
    ? "normal：到点后自动评审，自动修复（AI 修复写入前仍受权限与校验约束）"
    : "dry-run：只评审并写报告，不修改项目文件";
}
