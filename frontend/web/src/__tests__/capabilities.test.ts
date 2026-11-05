// Capability inventory helpers (design §17.3 P12). The page shows five
// independently loaded registries plus configuration problems; these tests pin
// the combined category + free-text filter so a broken search can never make a
// loaded capability look absent (or show an unrelated row as a match).

import { describe, expect, it } from "vitest";
import {
  CAPABILITY_CATEGORIES,
  capabilityCategoryCount,
  capabilityHeadline,
  filterCapabilities,
} from "../capabilities";
import type { CapabilitiesView } from "../types";

function view(overrides: Partial<CapabilitiesView> = {}): CapabilitiesView {
  return {
    project_root: "/repo",
    generated_at: "2026-11-05T00:00:00+00:00",
    summary: {
      tools: 2,
      commands: 1,
      skills: 1,
      plugins: 1,
      enabled_plugins: 1,
      mcp_servers: 1,
      mcp_errors: 0,
      problems: 0,
    },
    has_problems: false,
    problems: [],
    tools: [
      {
        name: "bash",
        description: "Run a shell command",
        required_args: ["command"],
        optional_args: ["timeout"],
      },
      {
        name: "file_read",
        description: "Read a file",
        required_args: ["path"],
        optional_args: [],
      },
    ],
    commands: [
      {
        name: "doctor",
        description: "Check machine readiness",
        behavior: "read_only",
        behavior_detail: "inspects state",
        remote_invocable: true,
        remote_admin_opt_in: false,
      },
    ],
    skills: [
      {
        name: "review",
        description: "Review a change",
        source: "bundled",
        path: "/skills/review.md",
        user_invocable: true,
        disable_model_invocation: false,
        model: "",
      },
    ],
    plugins: [
      {
        name: "demo",
        version: "1.0.0",
        description: "Demo plugin",
        enabled: true,
        path: "/plugins/demo",
        skills: 1,
        commands: 2,
        agents: 0,
        mcp_servers: 1,
      },
    ],
    mcp_servers: [
      {
        name: "filesystem",
        transport: "stdio",
        target: "npx -y server-filesystem /repo",
        status: "ok",
        issues: [],
        remedy: "",
      },
    ],
    ...overrides,
  };
}

describe("CAPABILITY_CATEGORIES", () => {
  it("offers exactly one chip per inventory section", () => {
    expect(CAPABILITY_CATEGORIES.map((category) => category.value)).toEqual([
      "all",
      "mcp",
      "skills",
      "plugins",
      "tools",
      "commands",
    ]);
  });
});

describe("filterCapabilities", () => {
  it("returns nothing before the first read", () => {
    const sections = filterCapabilities(null, "all", "");
    expect(sections.problems).toEqual([]);
    expect(sections.tools).toEqual([]);
    expect(sections.mcp).toEqual([]);
  });

  it("shows every section for 全部 with a blank query", () => {
    const sections = filterCapabilities(view(), "all", "   ");
    expect(sections.mcp).toHaveLength(1);
    expect(sections.skills).toHaveLength(1);
    expect(sections.plugins).toHaveLength(1);
    expect(sections.tools).toHaveLength(2);
    expect(sections.commands).toHaveLength(1);
  });

  it("keeps only the selected category", () => {
    const sections = filterCapabilities(view(), "tools", "");
    expect(sections.tools).toHaveLength(2);
    expect(sections.mcp).toEqual([]);
    expect(sections.skills).toEqual([]);
    expect(sections.plugins).toEqual([]);
    expect(sections.commands).toEqual([]);
  });

  it("matches names, descriptions, args, paths and issue text case-insensitively", () => {
    const inventory = view({
      problems: [
        {
          kind: "mcp",
          name: "broken",
          detail: "command not found in PATH: npx",
          remedy: "Install npx",
        },
      ],
    });
    expect(filterCapabilities(inventory, "tools", "SHELL").tools.map((row) => row.name)).toEqual([
      "bash",
    ]);
    expect(filterCapabilities(inventory, "tools", "path").tools.map((row) => row.name)).toEqual([
      "file_read",
    ]);
    expect(filterCapabilities(inventory, "mcp", "server-filesystem").mcp).toHaveLength(1);
    expect(filterCapabilities(inventory, "skills", "/skills/review.md").skills).toHaveLength(1);
    expect(filterCapabilities(inventory, "plugins", "2").plugins).toHaveLength(0);
    expect(filterCapabilities(inventory, "all", "not found").problems).toHaveLength(1);
  });

  it("keeps configuration problems visible while filtering another category", () => {
    const inventory = view({
      problems: [
        {
          kind: "loader",
          name: "plugins",
          detail: "ValueError: bad manifest",
          remedy: "Fix the manifest",
        },
      ],
    });
    const sections = filterCapabilities(inventory, "commands", "");
    expect(sections.commands).toHaveLength(1);
    expect(sections.problems).toHaveLength(1);
  });
});

describe("capabilityCategoryCount", () => {
  it("counts every row for 全部 and only that section otherwise", () => {
    const inventory = view();
    expect(capabilityCategoryCount(inventory, "all")).toBe(6);
    expect(capabilityCategoryCount(inventory, "tools")).toBe(2);
    expect(capabilityCategoryCount(inventory, "commands")).toBe(1);
    expect(capabilityCategoryCount(null, "all")).toBe(0);
  });
});

describe("capabilityHeadline", () => {
  it("says loading before the first read", () => {
    const headline = capabilityHeadline(null);
    expect(headline.tone).toBe("warn");
    expect(headline.label).toBe("尚未读取");
  });

  it("leads with the problem count when configuration is broken", () => {
    const headline = capabilityHeadline(
      view({
        has_problems: true,
        problems: [
          {
            kind: "mcp",
            name: "broken",
            detail: "invalid url",
            remedy: "Use https://…",
          },
        ],
      }),
    );
    expect(headline.tone).toBe("warn");
    expect(headline.label).toContain("1 个配置问题");
  });

  it("reports ready when nothing is broken", () => {
    const headline = capabilityHeadline(view());
    expect(headline.tone).toBe("ok");
    expect(headline.label).toBe("配置就绪");
  });
});
