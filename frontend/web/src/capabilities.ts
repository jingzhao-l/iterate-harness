// Capability inventory helpers (design §17.3 P12). The page shows five
// independently loaded registries; these pure functions own the one behavior
// that must stay predictable: what the search/filter actually hides. Both the
// category chips and the free-text search are applied together, and a blank
// query is "show everything", not "show nothing".

import type {
  CapabilitiesView,
  CapabilityCategory,
  CapabilityCommand,
  CapabilityMcpServer,
  CapabilityPlugin,
  CapabilityProblem,
  CapabilitySkill,
  CapabilityTool,
} from "./types";

export const CAPABILITY_CATEGORIES: Array<{
  value: CapabilityCategory;
  label: string;
}> = [
  { value: "all", label: "全部" },
  { value: "mcp", label: "MCP 服务" },
  { value: "skills", label: "技能" },
  { value: "plugins", label: "插件" },
  { value: "tools", label: "工具" },
  { value: "commands", label: "斜杠命令" },
];

export interface CapabilitySections {
  problems: CapabilityProblem[];
  mcp: CapabilityMcpServer[];
  skills: CapabilitySkill[];
  plugins: CapabilityPlugin[];
  tools: CapabilityTool[];
  commands: CapabilityCommand[];
}

function matches(query: string, ...fields: Array<string | string[] | undefined>): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return fields.some((field) => {
    const text = Array.isArray(field) ? field.join(" ") : (field ?? "");
    return text.toLowerCase().includes(needle);
  });
}

/**
 * Apply the category chip and the free-text query together.
 *
 * - `all` keeps every section; any other category empties the other sections so
 *   the page cannot render a filtered view that still shows unrelated rows.
 * - Search is case-insensitive and includes the fields an operator can see
 *   (name/description/source/path/args), so searching "pytest" finds a command
 *   even when the word only appears in its description.
 */
export function filterCapabilities(
  view: CapabilitiesView | null,
  category: CapabilityCategory,
  query: string,
): CapabilitySections {
  const empty: CapabilitySections = {
    problems: [],
    mcp: [],
    skills: [],
    plugins: [],
    tools: [],
    commands: [],
  };
  if (!view) return empty;

  const all = category === "all";
  return {
    problems: view.problems.filter((row) =>
      matches(query, row.kind, row.name, row.detail, row.remedy),
    ),
    mcp: all || category === "mcp"
      ? view.mcp_servers.filter((row) =>
          matches(query, row.name, row.transport, row.target, row.status, row.issues, row.remedy),
        )
      : [],
    skills: all || category === "skills"
      ? view.skills.filter((row) =>
          matches(query, row.name, row.description, row.source, row.path, row.model),
        )
      : [],
    plugins: all || category === "plugins"
      ? view.plugins.filter((row) =>
          matches(query, row.name, row.version, row.description, row.path),
        )
      : [],
    tools: all || category === "tools"
      ? view.tools.filter((row) =>
          matches(query, row.name, row.description, row.required_args, row.optional_args),
        )
      : [],
    commands: all || category === "commands"
      ? view.commands.filter((row) =>
          matches(query, row.name, row.description, row.behavior, row.behavior_detail),
        )
      : [],
  };
}

/** Count the rows a category represents, used in the chip labels. */
export function capabilityCategoryCount(
  view: CapabilitiesView | null,
  category: CapabilityCategory,
): number {
  if (!view) return 0;
  switch (category) {
    case "all":
      return (
        view.problems.length +
        view.mcp_servers.length +
        view.skills.length +
        view.plugins.length +
        view.tools.length +
        view.commands.length
      );
    case "mcp":
      return view.mcp_servers.length;
    case "skills":
      return view.skills.length;
    case "plugins":
      return view.plugins.length;
    case "tools":
      return view.tools.length;
    case "commands":
      return view.commands.length;
    default:
      return 0;
  }
}

/** One-line headline for the summary card. */
export function capabilityHeadline(view: CapabilitiesView | null): {
  tone: "ok" | "warn";
  label: string;
  detail: string;
} {
  if (!view) {
    return { tone: "warn", label: "尚未读取", detail: "加载能力清单…" };
  }
  const problems = view.problems.length;
  if (problems > 0) {
    return {
      tone: "warn",
      label: `${problems} 个配置问题`,
      detail: "能力仍可查看；按每条问题的修复方法处理后重新读取。",
    };
  }
  return {
    tone: "ok",
    label: "配置就绪",
    detail: "已加载的能力没有静态配置问题。",
  };
}
