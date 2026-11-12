// Lifecycle-hook display helpers (design §17.3 P15). The status label, the
// type badge class, the headline cards and the event label are pinned here so
// the console's outcome semantics agree with the backend's
// `HookLogEntry.success/blocked` flags, and a mixed log (pass / block / fail)
// never collapses into one ambiguous message.

import { describe, expect, it } from "vitest";
import {
  eventLabel,
  hookHeadline,
  hookRowStatus,
  hookTestCommand,
  hookTypeClass,
  statusLabel,
} from "../hooks";
import type { HookLogEntry, HooksView } from "../types";

function entry(overrides: Partial<HookLogEntry> = {}): HookLogEntry {
  return {
    timestamp: "2026-11-08T09:30:00+00:00",
    event: "pre_tool_use",
    hook_type: "command",
    matcher: "",
    detail: "printf 'booted'",
    model: "",
    success: true,
    blocked: false,
    reason: "",
    output: "",
    duration_ms: 5,
    returncode: 0,
    status_code: null,
    source: "settings",
    ...overrides,
  };
}

describe("statusLabel", () => {
  it("maps success / blocked / failure to distinct labels", () => {
    expect(statusLabel(entry({ success: true, blocked: false })).text).toBe("通过");
    expect(statusLabel(entry({ success: false, blocked: true })).text).toBe("拦截");
    expect(statusLabel(entry({ success: false, blocked: false })).text).toBe("失败");
  });

  it("uses the severity palette for each outcome", () => {
    expect(statusLabel(entry({ success: true })).cls).toBe("neutral");
    expect(statusLabel(entry({ success: false, blocked: true })).cls).toBe("warn");
    expect(statusLabel(entry({ success: false, blocked: false })).cls).toBe("danger");
  });
});

describe("hookTypeClass", () => {
  it("maps each hook type to a stable badge class", () => {
    expect(hookTypeClass("command")).toBe("hook-type-command");
    expect(hookTypeClass("agent")).toBe("hook-type-command");
    expect(hookTypeClass("prompt")).toBe("hook-type-prompt");
    expect(hookTypeClass("http")).toBe("hook-type-http");
    expect(hookTypeClass("")).toBe("hook-type-http");
  });
});

describe("hookHeadline", () => {
  it("reads the four cards from the summary", () => {
    const view: HooksView = {
      project_root: "/repo",
      generated_at: "2026-11-08T09:30:00+00:00",
      summary: {
        total: 3,
        events: 2,
        journal_total: 9,
        journal_failures: 2,
        journal_blocked: 1,
      },
      has_problems: false,
      problems: [],
      events: [],
      journal: {},
      journal_events: [],
      journal_types: [],
    };
    expect(hookHeadline(view)).toEqual({
      total: 3,
      events: 2,
      executed: 9,
      failures: 2,
      blocked: 1,
    });
  });

  it("is defensive about a missing summary", () => {
    const view: HooksView = {
      project_root: "/repo",
      generated_at: "",
      summary: {},
      has_problems: false,
      problems: [],
      events: [],
      journal: {},
      journal_events: [],
      journal_types: [],
    };
    expect(hookHeadline(view)).toEqual({
      total: 0,
      events: 0,
      executed: 0,
      failures: 0,
      blocked: 0,
    });
  });
});

describe("eventLabel", () => {
  it("humanizes snake_case events", () => {
    expect(eventLabel("pre_tool_use")).toBe("pre tool use");
    expect(eventLabel("session_start")).toBe("session start");
  });
});

describe("hookTestCommand", () => {
  it("supplies the payload field each event's matchers read", () => {
    expect(hookTestCommand("pre_tool_use")).toBe(
      "ih hooks test pre_tool_use --tool-name bash",
    );
    expect(hookTestCommand("post_tool_use")).toBe(
      "ih hooks test post_tool_use --tool-name bash",
    );
    expect(hookTestCommand("user_prompt_submit")).toBe(
      'ih hooks test user_prompt_submit --prompt "describe the task"',
    );
    expect(hookTestCommand("notification")).toBe(
      "ih hooks test notification --notification-type permission_prompt",
    );
    expect(hookTestCommand("session_start")).toBe("ih hooks test session_start");
  });
});

describe("hookRowStatus", () => {
  it("marks a hook with static issues as a problem", () => {
    expect(hookRowStatus([]).text).toBe("就绪");
    expect(hookRowStatus(undefined).cls).toBe("neutral");
    expect(hookRowStatus(["command not found on PATH: nope"]).text).toBe("问题");
    expect(hookRowStatus(["command not found on PATH: nope"]).cls).toBe("warn");
  });
});
