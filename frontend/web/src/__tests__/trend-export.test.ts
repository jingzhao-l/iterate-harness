// Trend export naming (design §17.3 P8). The server stamps the filename from
// the report's generated_at; this is the fallback used when a proxy strips
// Content-Disposition, so it must stay UTC and sortable.

import { describe, expect, it } from "vitest";
import { trendExportFilename } from "../trend";

describe("trendExportFilename", () => {
  it("stamps UTC time and the requested extension", () => {
    const at = new Date("2026-10-30T04:05:06Z");
    expect(trendExportFilename("md", at)).toBe("iterate-trend-20261030-040506.md");
    expect(trendExportFilename("csv", at)).toBe("iterate-trend-20261030-040506.csv");
    expect(trendExportFilename("json", at)).toBe("iterate-trend-20261030-040506.json");
  });

  it("pads every component so names sort lexicographically", () => {
    const at = new Date("2026-01-02T03:04:05Z");
    expect(trendExportFilename("csv", at)).toBe("iterate-trend-20260102-030405.csv");
  });
});
