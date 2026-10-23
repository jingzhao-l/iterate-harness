// Trend page aggregation rules (design §17.3 P8). These decide the headline
// "direction" card and the window totals, so they must not drift from the
// backend's per-run new/fixed/regressed counts.

import { describe, expect, it } from "vitest";
import {
  directionLabel,
  directionTone,
  netDirection,
  newestFirst,
  reportHasData,
  windowTotals,
} from "../trend";
import type { TrendReport, TrendRun } from "../types";

function run(overrides: Partial<TrendRun> = {}): TrendRun {
  return {
    index: 1,
    timestamp: "2026-01-01T00:00:00+00:00",
    round: 1,
    verdict: "approved",
    mode: "review",
    total: 3,
    reported_total: 3,
    new: 1,
    fixed: 2,
    regressed: 0,
    unchanged: 2,
    by_severity: {},
    by_dimension: {},
    ...overrides,
  };
}

function report(overrides: Partial<TrendReport> = {}): TrendReport {
  return {
    project_root: "/tmp/p",
    generated_at: "2026-01-03T00:00:00+00:00",
    has_data: true,
    total_runs: 0,
    tracked: 0,
    open: 0,
    fixed: 0,
    stubborn: 0,
    runs: [],
    latest: null,
    open_findings: [],
    stubborn_findings: [],
    open_by_dimension: {},
    ...overrides,
  };
}

describe("windowTotals", () => {
  it("sums only the visible window and includes regressions", () => {
    const runs = [
      run({ new: 3, fixed: 1, regressed: 0 }),
      run({ new: 0, fixed: 4, regressed: 1 }),
    ];
    expect(windowTotals(runs)).toEqual({ runs: 2, new: 3, fixed: 5, regressed: 1 });
  });

  it("is zeroed for an empty window", () => {
    expect(windowTotals([])).toEqual({ runs: 0, new: 0, fixed: 0, regressed: 0 });
  });
});

describe("netDirection", () => {
  it("counts fixed minus new minus regressed", () => {
    expect(netDirection([run({ new: 1, fixed: 4, regressed: 2 })])).toBe(1);
  });

  it("labels improvement, regression and flat windows distinctly", () => {
    expect(netDirection([run({ new: 1, fixed: 1, regressed: 0 })])).toBe(0);
    expect(directionLabel(3)).toBe("净改善 3");
    expect(directionLabel(-2)).toBe("净恶化 2");
    expect(directionLabel(0)).toBe("持平");
    expect(directionTone(3)).toBe("good");
    expect(directionTone(-3)).toBe("bad");
    expect(directionTone(0)).toBe("neutral");
  });
});

describe("newestFirst", () => {
  it("reverses a copy without mutating the API order", () => {
    const runs = [run({ index: 1 }), run({ index: 2 }), run({ index: 3 })];
    expect(newestFirst(runs).map((item) => item.index)).toEqual([3, 2, 1]);
    expect(runs.map((item) => item.index)).toEqual([1, 2, 3]);
  });
});

describe("reportHasData", () => {
  it("honours the backend flag, run history and library records", () => {
    expect(reportHasData(null)).toBe(false);
    expect(reportHasData(report({ has_data: false }))).toBe(false);
    expect(reportHasData(report({ has_data: false, runs: [run()] }))).toBe(true);
    expect(reportHasData(report({ has_data: false, tracked: 2 }))).toBe(true);
  });
});
