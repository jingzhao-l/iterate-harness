// Self-check helper tests (design §17.3 P11). The status labels and grouping
// rules drive what the operator sees first, so a mis-ordered report (a warn
// above a fail) is exactly what these pin.

import { describe, expect, it } from "vitest";
import {
  STATUS_LABELS,
  doctorHeadline,
  groupChecks,
  needsAttention,
  statusTone,
} from "../doctor";
import type { DoctorCheck, DoctorView } from "../types";

function check(id: string, status: DoctorCheck["status"]): DoctorCheck {
  return { id, title: id, status, detail: "", remedy: "" };
}

function view(checks: DoctorCheck[]): DoctorView {
  const summary: Record<string, number> = { ok: 0, warn: 0, fail: 0 };
  for (const c of checks) summary[c.status] += 1;
  return {
    project_root: "/p",
    generated_at: "2026-11-01T00:00:00Z",
    ok: summary.fail === 0,
    has_failures: summary.fail > 0,
    has_warnings: summary.warn > 0,
    summary,
    checks,
  };
}

describe("doctorHeadline", () => {
  it("leads with failures when any exist", () => {
    const report = view([
      check("auth", "fail"),
      check("storage", "warn"),
      check("python", "ok"),
    ]);
    const headline = doctorHeadline(report);
    expect(headline.tone).toBe("fail");
    expect(headline.label).toContain("1 项失败");
  });

  it("leads with warnings when only warnings exist", () => {
    const headline = doctorHeadline(view([check("hook", "warn"), check("python", "ok")]));
    expect(headline.tone).toBe("warn");
    expect(headline.label).toContain("告警");
  });

  it("reports ready when everything is ok", () => {
    const headline = doctorHeadline(view([check("python", "ok")]));
    expect(headline.tone).toBe("ok");
    expect(headline.label).toContain("就绪");
  });

  it("has its own label before the first run", () => {
    const headline = doctorHeadline(null);
    expect(headline.tone).toBe("warn");
    expect(headline.label).toBe("尚未检查");
  });
});

describe("groupChecks", () => {
  it("sorts failures → warnings → ok without reordering within a status", () => {
    const grouped = groupChecks([
      check("python", "ok"),
      check("auth", "fail"),
      check("hook", "warn"),
      check("config", "fail"),
    ]);
    expect(grouped.map((c) => c.status)).toEqual(["fail", "fail", "warn", "ok"]);
    expect(grouped.map((c) => c.id)).toEqual(["auth", "config", "hook", "python"]);
  });
});

describe("labels and tones", () => {
  it("maps each status to a stable label and CSS tone", () => {
    expect(STATUS_LABELS.fail).toBe("失败");
    expect(statusTone("fail")).toBe("severity-critical");
    expect(statusTone("warn")).toBe("amber");
    expect(statusTone("ok")).toBe("green");
  });

  it("flags only fail/warn as needing attention", () => {
    expect(needsAttention(check("a", "fail"))).toBe(true);
    expect(needsAttention(check("a", "warn"))).toBe(true);
    expect(needsAttention(check("a", "ok"))).toBe(false);
  });
});
