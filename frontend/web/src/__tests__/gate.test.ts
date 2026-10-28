// Recipe-gate page helpers (design §17.3 P9). The headline is the page's whole
// verdict, so its wording/tone must stay pinned — in particular the absence
// case, which must never read like a pass.

import { describe, expect, it } from "vitest";
import { gateHeadline, newestFirst, recordKey, shortDigest } from "../gate";
import type { GateRecord } from "../types";

function record(overrides: Partial<GateRecord> = {}): GateRecord {
  return {
    round: 1,
    timestamp: "2026-01-01T00:00:00+00:00",
    allowed: true,
    invariant_verdict: "pass",
    recipe_digest: "sha256:" + "a".repeat(64),
    steps: ["build", "test"],
    fix_proof: null,
    refusal: null,
    ...overrides,
  };
}

describe("gateHeadline", () => {
  it("labels absence as absence, not as a pass", () => {
    const headline = gateHeadline(null);
    expect(headline.label).toBe("尚未记录");
    expect(headline.tone).toBe("neutral");
    expect(headline.detail).toContain("不等于通过");
  });

  it("labels an allowed round and names the verdict", () => {
    const headline = gateHeadline(record({ round: 4, invariant_verdict: "pass" }));
    expect(headline.label).toBe("允许进入复验");
    expect(headline.tone).toBe("good");
    expect(headline.detail).toContain("第 4 轮");
    expect(headline.detail).toContain("pass");
  });

  it("prefers the refusal code when one was published", () => {
    const headline = gateHeadline(
      record({
        allowed: false,
        invariant_verdict: "fail",
        refusal: {
          code: "RECIPE_GATE_E_INVARIANT_GATE",
          message: "not a pass",
          remedy: "fix it",
        },
      }),
    );
    expect(headline.label).toBe("已拒绝");
    expect(headline.tone).toBe("bad");
    expect(headline.detail).toContain("RECIPE_GATE_E_INVARIANT_GATE");
  });

  it("falls back to the verdict when a refusal carries no code", () => {
    const headline = gateHeadline(
      record({ allowed: false, invariant_verdict: "unmeasured", refusal: null }),
    );
    expect(headline.detail).toContain("unmeasured");
  });
});

describe("newestFirst", () => {
  it("reverses a copy without mutating the API order", () => {
    const records = [record({ round: 1 }), record({ round: 2 }), record({ round: 3 })];
    expect(newestFirst(records).map((item) => item.round)).toEqual([3, 2, 1]);
    expect(records.map((item) => item.round)).toEqual([1, 2, 3]);
  });
});

describe("recordKey", () => {
  it("distinguishes the same round number recorded at different times", () => {
    expect(recordKey(record({ round: 2, timestamp: "a" }))).not.toBe(
      recordKey(record({ round: 2, timestamp: "b" })),
    );
  });
});

describe("shortDigest", () => {
  it("keeps the algorithm prefix and truncates the value", () => {
    expect(shortDigest("sha256:" + "a".repeat(64))).toBe("sha256:aaaaaaaaaaaa…");
  });

  it("leaves short values and explicit absence alone", () => {
    expect(shortDigest("sha256:abc")).toBe("sha256:abc");
    expect(shortDigest(null)).toBe("—");
    expect(shortDigest("")).toBe("—");
  });
});
