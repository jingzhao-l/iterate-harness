// Two-run comparison helpers (design §17.3 P2). The page can only be trusted
// if the pair stays ordered (earlier < later) and the section wording matches
// the engine's classification, so both are pinned here.

import { describe, expect, it } from "vitest";
import { DIFF_SECTIONS, diffSummary, normalizePair, runOptionLabel } from "../diff";
import type { RunSnapshotView } from "../types";

const run = (index: number, total = 2, verdict = "approved"): RunSnapshotView => ({
  index,
  timestamp: `2026-01-0${index}T04:05:06.000Z`,
  round: 1,
  verdict,
  mode: "review",
  total,
});

describe("runOptionLabel", () => {
  it("names the run by index, local time, verdict and finding count", () => {
    expect(runOptionLabel(run(2, 3))).toBe("#2 · 01-02 04:05 · approved · 3 条");
  });

  it("does not crash on a missing timestamp", () => {
    expect(runOptionLabel({ ...run(1), timestamp: "" })).toBe("#1 · ? · approved · 2 条");
  });
});

describe("normalizePair", () => {
  it("keeps an already ordered pair unchanged", () => {
    expect(normalizePair(2, 5, 7)).toEqual({ from: 2, to: 5 });
  });

  it("moves the later side when both sides name the same run", () => {
    expect(normalizePair(3, 3, 7)).toEqual({ from: 3, to: 4 });
  });

  it("swaps when the user picks the later run on the earlier side", () => {
    expect(normalizePair(5, 2, 7)).toEqual({ from: 5, to: 6 });
  });

  it("clamps out-of-range values and keeps a valid pair", () => {
    expect(normalizePair(0, 99, 4)).toEqual({ from: 1, to: 4 });
    expect(normalizePair(9, 9, 4)).toEqual({ from: 3, to: 4 });
  });

  it("leaves a single-run project alone (no pair exists)", () => {
    expect(normalizePair(1, 1, 1)).toEqual({ from: 1, to: 1 });
  });
});

describe("diffSummary", () => {
  it("renders the four counts in a stable order", () => {
    expect(diffSummary({ new: 1, fixed: 2, regressed: 3, unchanged: 4 })).toBe(
      "新增 1 · 修复 2 · 回归 3 · 未变 4",
    );
  });
});

describe("DIFF_SECTIONS", () => {
  it("covers exactly the four engine buckets, regressions first-class", () => {
    expect(DIFF_SECTIONS.map((section) => section.key)).toEqual([
      "new",
      "fixed",
      "regressed",
      "unchanged",
    ]);
    expect(DIFF_SECTIONS.find((section) => section.key === "regressed")?.tone).toBe(
      "severity-critical",
    );
  });
});
