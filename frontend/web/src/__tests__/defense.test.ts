// Defensive-kernel display helpers (design §17.3 P17). The headline order is
// the important contract: a dead configuration or a disabled/unmeasured guard
// outranks a stale "pass", because those are the states in which the guard
// protects nothing. The copyable commands are pinned so the page cannot drift
// from what `ih defense check|journal` accepts.

import { describe, expect, it } from "vitest";
import {
  assumptionStatusLabel,
  assumptionTone,
  defenseCheckCommand,
  defenseHeadline,
  defenseJournalCommand,
  eventResultLabel,
  kindLabel,
  outcomeLabel,
  outcomeTone,
  shortTimestamp,
} from "../defense";
import type { DefenseEvent, DefenseView } from "../types";

function event(overrides: Partial<DefenseEvent> = {}): DefenseEvent {
  return {
    id: "df_1",
    timestamp: "2026-11-24T09:30:00+00:00",
    kind: "invariant",
    outcome: "pass",
    tool: "write_file",
    path: "src/a.py",
    trigger: "invariant-pass",
    reason: "",
    checks_run: 1,
    duration_ms: 4,
    rolled_back: false,
    violations: [],
    ...overrides,
  };
}

function view(overrides: Partial<DefenseView> = {}): DefenseView {
  return {
    project_root: "/repo",
    generated_at: "2026-11-24T09:30:00+00:00",
    has_problems: false,
    problems: [],
    policy: {
      source: "invariants",
      source_label: "invariants",
      configured: true,
      enabled: true,
      ensure: ["README.md"],
      commands: {},
      modules: 0,
      checks: 1,
      dropped_empty_modules: [],
      notes: [],
    },
    assumptions: [],
    assumption_summary: { total: 0, declared: 0, holds: 0, falsified: 0 },
    journal: {
      total: 0,
      rollbacks: 0,
      invariant_runs: 0,
      manual_checks: 0,
      last_verdict: "",
      last_event_at: "",
      by_kind: {},
      by_outcome: {},
    },
    recent_events: [],
    summary: {
      checks: 1,
      modules: 0,
      rollbacks: 0,
      invariant_runs: 0,
      last_verdict: "pass",
      last_event_at: "",
      assumptions: 0,
      falsified_assumptions: 0,
    },
    journal_kinds: [],
    journal_outcomes: [],
    journal_tools: [],
    ...overrides,
  };
}

describe("defenseHeadline", () => {
  it("reads as loading before the first response", () => {
    expect(defenseHeadline(null)).toEqual({
      label: "读取中",
      detail: "正在解析防御守卫…",
      tone: "neutral",
    });
  });

  it("leads with dead entries before anything else", () => {
    const headline = defenseHeadline(
      view({
        has_problems: true,
        problems: [{ kind: "command", name: "core", detail: "d", remedy: "r" }],
      }),
    );
    expect(headline.label).toBe("1 项配置问题");
    expect(headline.tone).toBe("warn");
  });

  it("distinguishes an explicit disable from never configured", () => {
    const disabled = defenseHeadline(
      view({
        policy: {
          ...view().policy,
          source: "disabled",
          enabled: false,
          checks: 0,
        },
      }),
    );
    expect(disabled.label).toBe("守卫已显式关闭");
    expect(disabled.tone).toBe("warn");

    const none = defenseHeadline(
      view({
        policy: {
          ...view().policy,
          source: "none",
          enabled: false,
          checks: 0,
        },
      }),
    );
    expect(none.label).toBe("未配置守卫");
    expect(none.tone).toBe("warn");
  });

  it("does not report a stale pass as healthy after a failed verdict", () => {
    const headline = defenseHeadline(
      view({ summary: { ...view().summary, rollbacks: 2, last_verdict: "fail" } }),
    );
    expect(headline.label).toBe("最近判定：失败");
    expect(headline.tone).toBe("warn");
  });

  it("reports the ready guard when it is clean", () => {
    const headline = defenseHeadline(view());
    expect(headline.label).toBe("1 个检查就绪");
    expect(headline.tone).toBe("ok");
  });
});

describe("event labels and tones", () => {
  it("marks a rollback as rolled back instead of a bare fail", () => {
    const label = eventResultLabel(
      event({ outcome: "fail", rolled_back: true }),
    );
    expect(label).toBe("已回滚 · 失败");
  });

  it("never treats unmeasured as a pass", () => {
    expect(eventResultLabel(event({ outcome: "unmeasured" }))).toBe("未测量");
    expect(outcomeTone("unmeasured")).toBe("amber");
    expect(outcomeTone("pass")).toBe("green");
    expect(outcomeTone("fail")).toBe("severity-high");
    expect(outcomeTone("pass", true)).toBe("severity-high");
  });

  it("keeps unknown values visible", () => {
    expect(kindLabel("future-kind")).toBe("future-kind");
    expect(outcomeLabel("future-outcome")).toBe("future-outcome");
    expect(kindLabel("rollback")).toBe("回滚");
    expect(outcomeLabel("failed_tool")).toBe("工具失败");
  });

  it("maps assumption states", () => {
    expect(assumptionStatusLabel("falsified")).toBe("已证伪");
    expect(assumptionTone("falsified")).toBe("severity-high");
    expect(assumptionTone("holds")).toBe("green");
    expect(assumptionTone("declared")).toBe("amber");
  });
});

describe("copyable commands", () => {
  it("quotes a project root with spaces and omits an empty one", () => {
    expect(defenseCheckCommand("/repo")).toBe("ih defense check --project-root /repo");
    expect(defenseCheckCommand("")).toBe("ih defense check");
    expect(defenseCheckCommand("/a repo/it's")).toBe(
      "ih defense check --project-root '/a repo/it'\\''s'",
    );
  });

  it("mirrors the journal filters and format", () => {
    expect(
      defenseJournalCommand("/repo", { kind: "rollback", outcome: "fail", tool: "write_file" }),
    ).toBe(
      "ih defense journal --project-root /repo --kind rollback --outcome fail --tool write_file --format markdown",
    );
  });

  it("shortens timestamps without dropping old dates", () => {
    expect(shortTimestamp("2026-11-24T09:30:00+00:00")).toBe("11-24 09:30:00");
    expect(shortTimestamp("")).toBe("—");
  });
});
