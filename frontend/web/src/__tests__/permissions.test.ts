// Permission policy helpers (design §17.3 P16). These tests pin the labels and
// the copyable CLI command so the page cannot drift from what `ih permissions
// check` actually accepts (the equivalent command must round-trip every field).

import { describe, expect, it } from "vitest";
import {
  MODE_OPTIONS,
  PERMISSION_PRESETS,
  VERDICT_LABELS,
  pathRuleLabel,
  permissionCheckCommand,
  permissionHeadline,
  permissionPreset,
  readOnlySourceLabel,
  verdictTone,
} from "../permissions";
import type { PermissionsView } from "../types";

function view(overrides: Partial<PermissionsView> = {}): PermissionsView {
  return {
    project_root: "/repo",
    generated_at: "2026-11-16T00:00:00+00:00",
    mode: "default",
    mode_label: "默认",
    mode_detail: "Mutating tools require confirmation.",
    iterate_enabled: true,
    summary: {
      allowed_tools: 0,
      denied_tools: 0,
      path_rules: 4,
      deny_path_rules: 4,
      allow_path_rules: 0,
      allowed_commands: 0,
      denied_commands: 0,
      sensitive_paths: 17,
      risk_areas: 0,
      forbidden_patterns: 0,
      tools: 45,
      problems: 0,
    },
    has_problems: false,
    problems: [],
    allowed_tools: [],
    denied_tools: [],
    path_rules: [{ pattern: "*/.env", allow: false }],
    allowed_commands: [],
    denied_commands: [],
    sensitive_paths: ["*/.env"],
    risk_areas: [],
    forbidden_patterns: [],
    tools: [{ name: "read_file", description: "Read", read_only: true }],
    ...overrides,
  };
}

describe("permissionHeadline", () => {
  it("leads with configuration problems before the mode", () => {
    const headline = permissionHeadline(
      view({
        has_problems: true,
        problems: [
          { kind: "path_rule", name: "x", detail: "d", remedy: "r" },
        ],
      }),
    );
    expect(headline.label).toBe("1 项配置问题");
    expect(headline.tone).toBe("warn");
  });

  it("marks full_auto as a deliberate warning, not a success", () => {
    const headline = permissionHeadline(
      view({ mode: "full_auto", mode_label: "全自动" }),
    );
    expect(headline.label).toBe("全自动模式");
    expect(headline.tone).toBe("warn");
  });

  it("reports the configured mode when the policy is clean", () => {
    expect(permissionHeadline(view()).tone).toBe("ok");
    expect(permissionHeadline(null).tone).toBe("neutral");
  });
});

describe("labels", () => {
  it("maps verdicts to badges", () => {
    expect(VERDICT_LABELS.allowed).toBe("允许");
    expect(verdictTone("confirm")).toBe("amber");
    expect(verdictTone("denied")).toBe("severity-high");
  });

  it("explains how read-only was decided", () => {
    expect(readOnlySourceLabel("inferred")).toContain("推断");
    expect(readOnlySourceLabel("assumed")).toContain("可写");
    expect(readOnlySourceLabel("explicit")).toContain("显式");
  });

  it("labels path rules by effect", () => {
    expect(pathRuleLabel({ pattern: "x", allow: false })).toBe("拒绝");
    expect(pathRuleLabel({ pattern: "x", allow: true })).toBe("允许");
  });
});

describe("permissionCheckCommand", () => {
  it("renders a copyable command with every field", () => {
    const command = permissionCheckCommand({
      tool: "bash",
      path: "db/migrations/001.sql",
      command: "git push --force origin main",
      content: "DROP TABLE users;",
      mode: "full_auto",
      readOnly: false,
    });
    expect(command).toContain("ih permissions check bash");
    expect(command).toContain("--path db/migrations/001.sql");
    expect(command).toContain("--command 'git push --force origin main'");
    expect(command).toContain("--content 'DROP TABLE users;'");
    expect(command).toContain("--mode full_auto");
    expect(command).toContain("--mutating");
  });

  it("omits empty fields and uses --read-only only when forced", () => {
    expect(permissionCheckCommand({ tool: "read_file" })).toBe(
      "ih permissions check read_file",
    );
    expect(
      permissionCheckCommand({ tool: "read_file", readOnly: true }),
    ).toContain("--read-only");
  });

  it("escapes single quotes so the command survives a shell", () => {
    const command = permissionCheckCommand({
      tool: "bash",
      command: "echo 'hi'",
    });
    expect(command).toContain("'echo '\\''hi'\\'''");
  });
});

describe("presets", () => {
  it("exposes unique, runnable scenarios", () => {
    const ids = PERMISSION_PRESETS.map((preset) => preset.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const preset of PERMISSION_PRESETS) {
      expect(preset.input.tool).not.toBe("");
      expect(preset.label).not.toBe("");
    }
    expect(PERMISSION_PRESETS.some((preset) => preset.input.path === ".env")).toBe(
      true,
    );
  });

  it("returns null for an unknown preset instead of throwing", () => {
    expect(permissionPreset("nope")).toBeNull();
    expect(permissionPreset("sensitive-read")?.input.tool).toBe("read_file");
  });

  it("offers every mode plus the configured default", () => {
    expect(MODE_OPTIONS.map((option) => option.value)).toEqual([
      "",
      "default",
      "plan",
      "full_auto",
    ]);
  });
});
