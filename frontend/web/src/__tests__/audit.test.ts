// Audit-trail helper tests (design §17.3 P13). The filter query and display
// labels drive what the operator sees and what the export carries, so the
// mapping rules are pinned here without a DOM.

import { describe, expect, it } from "vitest";
import { actionHead, formatTimestamp, summaryText, toIsoLocal } from "../audit";

describe("toIsoLocal", () => {
  it("returns an ISO timestamp that represents the same wall-clock time", () => {
    const iso = toIsoLocal("2026-11-06T09:30");
    // The machine's local timezone may be anything; the invariant is that the
    // converted instant equals the wall-clock the operator typed.
    expect(new Date(iso).getTime()).toBe(new Date("2026-11-06T09:30").getTime());
    expect(Number.isNaN(new Date(iso).getTime())).toBe(false);
    expect(iso).toMatch(/T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/);
  });

  it("is empty for an empty value", () => {
    expect(toIsoLocal("")).toBe("");
  });

  it("is empty for an unparsable value", () => {
    expect(toIsoLocal("not-a-date")).toBe("");
  });
});

describe("actionHead", () => {
  it("splits a dotted action into its prefix", () => {
    expect(actionHead("config.save")).toBe("config");
    expect(actionHead("schedule.upsert")).toBe("schedule");
  });

  it("keeps a bare action intact", () => {
    expect(actionHead("config")).toBe("config");
    expect(actionHead("")).toBe("");
  });
});

describe("formatTimestamp", () => {
  it("renders a console-friendly day/time", () => {
    expect(formatTimestamp("2026-11-06T09:30:00+08:00")).toBe("11-06 09:30");
  });

  it("renders an em-dash for a missing timestamp", () => {
    expect(formatTimestamp("")).toBe("—");
  });
});

describe("summaryText", () => {
  it("serializes a summary mapping", () => {
    expect(summaryText({ summary: { removed: 2 } })).toBe('{"removed":2}');
  });

  it("is empty when there is no summary", () => {
    expect(summaryText({})).toBe("");
  });
});
