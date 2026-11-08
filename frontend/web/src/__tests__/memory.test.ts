// Memory-bank display helpers (design §17.3 P14). The byte formatting, the
// server's slug rule, the "which rows to show" decision and the headline card
// are pinned here: the console's conflict test must agree with the backend's
// `add_memory_entry` slug, and the empty/query states must not collapse into
// one "no data" message.

import { describe, expect, it } from "vitest";
import {
  formatModified,
  humanBytes,
  memoryHeadline,
  memorySlug,
  visibleEntries,
} from "../memory";
import type { MemoryEntry, MemoryView } from "../types";

function entry(name: string): MemoryEntry {
  return {
    name,
    title: name.replace(".md", ""),
    description: "",
    type: "",
    modified_at: "2026-11-07T09:30:00+00:00",
    body_preview: "",
    bytes: 10,
    path: `/memory/${name}`,
  };
}

function baseView(overrides: Partial<MemoryView> = {}): MemoryView {
  return {
    project_root: "/repo",
    generated_at: "2026-11-07T09:30:00+00:00",
    memory_dir: "/data/memory/repo-abc",
    exists: true,
    query: "",
    prompt: "",
    summary: { entries: 1, bytes: 10, enabled: true, max_files: 5, max_entrypoint_lines: 200 },
    entrypoint: { name: "MEMORY.md", path: "/data/memory/repo-abc/MEMORY.md", exists: true, lines: 2, bytes: 40, preview: "- [a](a.md)" },
    entries: [entry("a.md")],
    matches: [],
    injection: null,
    problems: [],
    ...overrides,
  };
}

describe("humanBytes", () => {
  it("formats bytes, KB and MB", () => {
    expect(humanBytes(0)).toBe("0 B");
    expect(humanBytes(10)).toBe("10 B");
    expect(humanBytes(2048)).toBe("2.0 KB");
    expect(humanBytes(2 * 1024 * 1024)).toBe("2.0 MB");
  });

  it("is defensive about non-finite values", () => {
    expect(humanBytes(Number.NaN)).toBe("0 B");
    expect(humanBytes(-5)).toBe("0 B");
  });
});

describe("memorySlug", () => {
  it("matches the backend slug rule", () => {
    expect(memorySlug("pytest quirks")).toBe("pytest_quirks.md");
    expect(memorySlug("  Deploy Notes  ")).toBe("deploy_notes.md");
    expect(memorySlug("!!!")).toBe("memory.md");
    expect(memorySlug("Pytest / CI: flaky")).toBe("pytest_ci_flaky.md");
  });
});

describe("formatModified", () => {
  it("renders a console-friendly day/time", () => {
    expect(formatModified("2026-11-07T09:30:00+00:00")).toBe("2026-11-07 09:30");
  });
  it("renders an em-dash for a missing timestamp", () => {
    expect(formatModified("")).toBe("—");
  });
});

describe("visibleEntries", () => {
  it("shows entries when there is no query", () => {
    expect(visibleEntries(baseView()).map((e) => e.name)).toEqual(["a.md"]);
  });

  it("shows ranked matches while a query is active", () => {
    const view = baseView({ query: "pytest", matches: [entry("b.md"), entry("a.md")] });
    expect(visibleEntries(view).map((e) => e.name)).toEqual(["b.md", "a.md"]);
  });

  it("shows an empty list (not all entries) when a query matched nothing", () => {
    const view = baseView({ query: "nope", matches: [], entries: [entry("a.md")] });
    expect(visibleEntries(view)).toEqual([]);
  });
});

describe("memoryHeadline", () => {
  it("leads with the uncreated state when the bank is empty", () => {
    const view = baseView({ exists: false, entries: [] });
    expect(memoryHeadline(view)).toMatchObject({ label: "尚未创建", tone: "warn" });
  });

  it("leads with problems when any exist", () => {
    const view = baseView({
      problems: [{ name: "dead.md", detail: "missing", remedy: "ih memory remove dead" }],
    });
    expect(memoryHeadline(view)).toMatchObject({ label: "需处理 1 项", tone: "warn" });
  });

  it("is ready when clean", () => {
    expect(memoryHeadline(baseView())).toMatchObject({ label: "就绪", tone: "ok" });
  });
});
