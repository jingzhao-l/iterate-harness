// Report download fallbacks (design §17.3 P7). The server normally supplies
// both headers; these keep the download usable behind a header-stripping proxy.

import { describe, expect, it } from "vitest";
import { reportContentType, reportDownloadName } from "../report";

describe("reportDownloadName", () => {
  it("keeps the artifact filename", () => {
    expect(reportDownloadName("report.html")).toBe("report.html");
    expect(reportDownloadName("report.csv")).toBe("report.csv");
  });

  it("drops any path components", () => {
    expect(reportDownloadName("../../etc/passwd")).toBe("passwd");
    expect(reportDownloadName("nested\\report.html")).toBe("report.html");
  });

  it("falls back to a report filename for empty input", () => {
    expect(reportDownloadName("")).toBe("report.html");
    expect(reportDownloadName("   ")).toBe("report.html");
  });
});

describe("reportContentType", () => {
  it("serves HTML reports as HTML", () => {
    expect(reportContentType("report.html")).toBe("text/html; charset=utf-8");
    expect(reportContentType("replay.html")).toBe("text/html; charset=utf-8");
  });

  it("serves CSV as text/csv", () => {
    expect(reportContentType("report.csv")).toBe("text/csv; charset=utf-8");
    expect(reportContentType("REPORT.CSV")).toBe("text/csv; charset=utf-8");
  });
});
