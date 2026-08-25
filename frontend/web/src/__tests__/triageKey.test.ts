// Runs.tsx triageKey unit tests — the dedup key must match the backend's
// file:::line:::dimension composition exactly (design §17.3 P2 / §17.9 gates).

import { describe, expect, it, vi } from "vitest";
import { triageKey } from "../pages/Runs";

// Runs.tsx imports ./api, whose module scope touches window/sessionStorage —
// unavailable in the node test environment, so stub it before the import.
vi.mock("../api", () => ({
  api: {
    status: vi.fn(),
    timeline: vi.fn(),
    findings: vi.fn(),
    triageDecisions: vi.fn(),
    triageFinding: vi.fn(),
    clearTriage: vi.fn(),
    chatStatus: vi.fn(),
    chatHistory: vi.fn(),
    chatStart: vi.fn(),
    chatSend: vi.fn(),
    chatControl: vi.fn(),
  },
  webuiToken: () => "",
}));

describe("triageKey", () => {
  it("joins file, line and dimension with ':::' separators", () => {
    expect(
      triageKey({ file: "src/a.ts", line: 42, dimension: "correctness" }),
    ).toBe("src/a.ts:::42:::correctness");
  });

  it("normalises undefined fields to empty segments", () => {
    expect(triageKey({})).toBe("::::::");
    expect(triageKey({ file: "a.py" })).toBe("a.py::::::");
    expect(triageKey({ file: "a.py", dimension: "perf" })).toBe("a.py::::::perf");
    expect(triageKey({ dimension: "perf" })).toBe("::::::perf");
  });

  it("keeps zero and empty-string segments verbatim", () => {
    expect(triageKey({ file: "b.ts", line: 0, dimension: "d" })).toBe("b.ts:::0:::d");
    expect(triageKey({ file: "", line: 7, dimension: "" })).toBe(":::7:::");
  });

  it("produces distinct keys for neighbouring findings", () => {
    const a = triageKey({ file: "x.ts", line: 1, dimension: "d" });
    const b = triageKey({ file: "x.ts", line: 2, dimension: "d" });
    expect(a).not.toBe(b);
  });
});
