// Capability inventory (design §17.3 P12) — the console's projection of
// `ih capabilities`. Before this page, the only way to see which tools,
// slash commands, skills, plugins and MCP servers a project actually loaded
// was a full `ih --dry-run` JSON dump; a broken MCP executable only surfaced
// as a failed call inside a live run. This page is read-only and network-free:
// it lists the loaded registries plus static MCP configuration problems, each
// with a copyable remedy.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  CAPABILITY_CATEGORIES,
  capabilityCategoryCount,
  capabilityHeadline,
  filterCapabilities,
} from "../capabilities";
import { useWebUi } from "../store";
import type {
  CapabilitiesView,
  CapabilityCategory,
  CapabilityCommand,
  CapabilityMcpServer,
  CapabilityPlugin,
  CapabilityProblem,
  CapabilitySkill,
  CapabilityTool,
} from "../types";

const BEHAVIOR_LABELS: Record<string, string> = {
  read_only: "只读",
  stateful: "会改状态",
  unknown: "未知",
};

const SOURCE_LABELS: Record<string, string> = {
  bundled: "内置",
  user: "用户",
  plugin: "插件",
  project: "项目",
};

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function copyText(
  text: string,
  onDone: (ok: boolean) => void,
): void {
  if (!navigator.clipboard?.writeText) {
    onDone(false);
    return;
  }
  void navigator.clipboard
    .writeText(text)
    .then(() => onDone(true))
    .catch(() => onDone(false));
}

function CopyButton({
  text,
  label = "复制",
}: {
  text: string;
  label?: string;
}): React.JSX.Element {
  const pushToast = useWebUi((state) => state.pushToast);
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn small"
      type="button"
      onClick={() =>
        copyText(text, (ok) => {
          if (!ok) {
            pushToast("error", "无法写入剪贴板，请手动选择文本");
            return;
          }
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        })
      }
      disabled={!text}
    >
      {copied ? "已复制" : label}
    </button>
  );
}

function ProblemRow({ problem }: { problem: CapabilityProblem }): React.JSX.Element {
  return (
    <tr className="row-warn">
      <td>
        <span className="badge severity-medium">{problem.kind}</span>
      </td>
      <td className="mono strong">{problem.name}</td>
      <td>{problem.detail}</td>
      <td>
        {problem.remedy ? (
          <div className="remedy-row">
            <code className="remedy">{problem.remedy}</code>
            <CopyButton text={problem.remedy} label="复制修复方法" />
          </div>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

function McpRow({ server }: { server: CapabilityMcpServer }): React.JSX.Element {
  return (
    <tr className={server.status === "ok" ? "row-dim" : "row-warn"}>
      <td className="mono strong">{server.name}</td>
      <td>{server.transport}</td>
      <td className="capability-target" title={server.target}>
        {server.target}
      </td>
      <td>
        <span className={`badge ${server.status === "ok" ? "green" : "severity-high"}`}>
          {server.status === "ok" ? "配置有效" : "需要处理"}
        </span>
      </td>
      <td>
        {server.issues.length > 0 ? (
          <div className="remedy-row">
            <code className="remedy">{server.issues.join("; ")}</code>
            {server.remedy && <CopyButton text={server.remedy} />}
          </div>
        ) : (
          <span className="muted">未发现静态配置问题（未连接服务器）</span>
        )}
      </td>
    </tr>
  );
}

function SkillRow({ skill }: { skill: CapabilitySkill }): React.JSX.Element {
  return (
    <tr>
      <td className="mono strong">{skill.name}</td>
      <td>{skill.description || "—"}</td>
      <td>
        <span className="badge neutral">
          {SOURCE_LABELS[skill.source] ?? (skill.source || "未知")}
        </span>
      </td>
      <td>
        {skill.user_invocable ? "可手动调用" : "仅模型可调用"}
        {skill.disable_model_invocation ? " · 禁用模型调用" : ""}
      </td>
      <td className="mono capability-path" title={skill.path}>
        {skill.path || "—"}
      </td>
    </tr>
  );
}

function PluginRow({ plugin }: { plugin: CapabilityPlugin }): React.JSX.Element {
  return (
    <tr className={plugin.enabled ? "" : "row-dim"}>
      <td className="mono strong">{plugin.name}</td>
      <td className="mono">{plugin.version}</td>
      <td>
        <span className={`badge ${plugin.enabled ? "green" : "gray"}`}>
          {plugin.enabled ? "已启用" : "已停用"}
        </span>
      </td>
      <td>{plugin.description || "—"}</td>
      <td className="mono">
        {plugin.skills} / {plugin.commands} / {plugin.agents} / {plugin.mcp_servers}
      </td>
      <td className="mono capability-path" title={plugin.path}>
        {plugin.path}
      </td>
    </tr>
  );
}

function ToolRow({ tool }: { tool: CapabilityTool }): React.JSX.Element {
  return (
    <tr>
      <td className="mono strong">{tool.name}</td>
      <td>{tool.description || "—"}</td>
      <td className="mono">
        {tool.required_args.length > 0 ? tool.required_args.join(", ") : "—"}
      </td>
      <td className="mono">{tool.optional_args.length > 0 ? tool.optional_args.join(", ") : "—"}</td>
    </tr>
  );
}

function CommandRow({ command }: { command: CapabilityCommand }): React.JSX.Element {
  return (
    <tr>
      <td className="mono strong">/{command.name}</td>
      <td>{command.description || "—"}</td>
      <td>
        <span className={`badge ${command.behavior === "read_only" ? "green" : "neutral"}`}>
          {BEHAVIOR_LABELS[command.behavior] ?? command.behavior}
        </span>
      </td>
      <td>{command.remote_invocable ? "可远程调用" : "仅本地"}</td>
    </tr>
  );
}

function Section({
  title,
  note,
  empty,
  children,
}: {
  title: string;
  note: string;
  empty: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="panel capability-section">
      <h2>{title}</h2>
      <p className="muted">{note}</p>
      {children ? children : <p className="empty">{empty}</p>}
    </section>
  );
}

export default function Capabilities(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const pushToast = useWebUi((state) => state.pushToast);
  const [view, setView] = useState<CapabilitiesView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<CapabilityCategory>("all");
  const [query, setQuery] = useState("");

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.capabilities(projectRoot));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      pushToast("error", `读取能力清单失败：${message}`);
    } finally {
      setLoading(false);
    }
  }, [projectRoot, pushToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const headline = capabilityHeadline(view);
  const sections = filterCapabilities(view, category, query);
  const visibleCount =
    sections.problems.length +
    sections.mcp.length +
    sections.skills.length +
    sections.plugins.length +
    sections.tools.length +
    sections.commands.length;

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">能力清单</h1>
        <p className="page-sub">
          本项目实际加载的工具、斜杠命令、技能、插件与 MCP 配置（与 `ih capabilities` 同源）
        </p>
      </div>
      <button className="btn" onClick={() => void load()} disabled={loading}>
        {loading ? "读取中…" : "重新读取"}
      </button>
    </div>
  );

  if (loading && !view) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 加载能力清单…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取能力清单失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!view) return <>{header}</>;

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次结果）：{error}</p>}

      <div className="cards">
        <div className="card">
          <div className="k">状态</div>
          <div className={`v small direction-${headline.tone === "ok" ? "ok" : "warn"}`}>
            {headline.label}
          </div>
          <div className="muted card-note">{headline.detail}</div>
        </div>
        <div className="card">
          <div className="k">工具</div>
          <div className="v small mono">{view.summary.tools ?? 0}</div>
          <div className="muted card-note">模型可调用的工具</div>
        </div>
        <div className="card">
          <div className="k">斜杠命令</div>
          <div className="v small mono">{view.summary.commands ?? 0}</div>
          <div className="muted card-note">终端 / REPL 命令</div>
        </div>
        <div className="card">
          <div className="k">技能</div>
          <div className="v small mono">{view.summary.skills ?? 0}</div>
          <div className="muted card-note">内置 + 用户 + 插件</div>
        </div>
        <div className="card">
          <div className="k">插件</div>
          <div className="v small mono">
            {view.summary.enabled_plugins ?? 0}/{view.summary.plugins ?? 0}
          </div>
          <div className="muted card-note">已启用 / 已发现</div>
        </div>
        <div className="card">
          <div className="k">MCP 服务</div>
          <div className="v small mono">{view.summary.mcp_servers ?? 0}</div>
          <div className="muted card-note">
            {view.summary.mcp_errors ? `${view.summary.mcp_errors} 个配置错误` : "静态检查无错误"}
          </div>
        </div>
      </div>

      {view.problems.length > 0 && (
        <section className="panel capability-section">
          <h2>需要处理（{view.problems.length}）</h2>
          <p className="muted">
            这些能力当前不可用或配置不完整。修复后点「重新读取」，不必等到下一次运行才发现。
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>名称</th>
                  <th>问题</th>
                  <th>修复方法</th>
                </tr>
              </thead>
              <tbody>
                {sections.problems.map((problem) => (
                  <ProblemRow key={`${problem.kind}:${problem.name}`} problem={problem} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="filter-row capability-filters">
        <input
          type="search"
          placeholder="搜索名称、描述、来源或参数…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="搜索能力"
        />
        <div className="chip-row" role="group" aria-label="能力类别">
          {CAPABILITY_CATEGORIES.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`chip ${category === option.value ? "active" : ""}`}
              aria-pressed={category === option.value}
              onClick={() => setCategory(option.value)}
            >
              {option.label}（{capabilityCategoryCount(view, option.value)}）
            </button>
          ))}
        </div>
        <span className="muted">匹配 {visibleCount} 项</span>
      </div>

      {(category === "all" || category === "mcp") && (
        <Section
          title={`MCP 服务（${sections.mcp.length}）`}
          note="静态检查命令是否存在、URL 是否合法、cwd 是否存在；不会启动服务器，也不消耗网络。"
          empty={
            view.mcp_servers.length === 0
              ? "尚未配置 MCP 服务器。在 settings.json 的 mcp_servers 或插件的 mcp.json 中配置后，此处会列出传输方式与校验结果。"
              : "没有匹配的 MCP 服务。"
          }
        >
          {sections.mcp.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>传输</th>
                    <th>目标</th>
                    <th>状态</th>
                    <th>检查结果</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.mcp.map((server) => (
                    <McpRow key={server.name} server={server} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {(category === "all" || category === "skills") && (
        <Section
          title={`技能（${sections.skills.length}）`}
          note="内置、用户目录与已启用插件贡献的技能；模型可见的指令与可手动调用的 /命令。"
          empty={
            view.skills.length === 0
              ? "没有加载任何技能。检查内置包或用户技能目录；插件技能需插件已启用。"
              : "没有匹配的技能。"
          }
        >
          {sections.skills.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>描述</th>
                    <th>来源</th>
                    <th>调用方式</th>
                    <th>路径</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.skills.map((skill) => (
                    <SkillRow key={`${skill.name}:${skill.path}`} skill={skill} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {(category === "all" || category === "plugins") && (
        <Section
          title={`插件（${sections.plugins.length}）`}
          note="发现的插件及其贡献数量；已停用的插件仍会列出，但不会向运行注入技能、命令、agent 或 MCP 服务。"
          empty={
            view.plugins.length === 0
              ? "没有发现插件。将插件放入插件目录并在设置中启用后，此处会显示它贡献的能力。"
              : "没有匹配的插件。"
          }
        >
          {sections.plugins.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>版本</th>
                    <th>状态</th>
                    <th>描述</th>
                    <th>技能/命令/agent/MCP</th>
                    <th>路径</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.plugins.map((plugin) => (
                    <PluginRow key={plugin.name} plugin={plugin} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {(category === "all" || category === "tools") && (
        <Section
          title={`工具（${sections.tools.length}）`}
          note="模型在当前进程里可调用的工具；必填与可选参数来自工具自己的 JSON Schema。MCP 工具按服务器连接时动态发现，不在此静态清单中。"
          empty={
            view.tools.length === 0
              ? "工具注册表为空——这是安装损坏的信号，请运行 `ih doctor`。"
              : "没有匹配的工具。"
          }
        >
          {sections.tools.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>描述</th>
                    <th>必填参数</th>
                    <th>可选参数</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.tools.map((tool) => (
                    <ToolRow key={tool.name} tool={tool} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {(category === "all" || category === "commands") && (
        <Section
          title={`斜杠命令（${sections.commands.length}）`}
          note="当前进程注册的斜杠命令；「会改状态」的命令可能修改配置、排队任务或启动一轮模型调用。"
          empty={
            view.commands.length === 0
              ? "命令注册表为空——这是安装损坏的信号，请运行 `ih doctor`。"
              : "没有匹配的斜杠命令。"
          }
        >
          {sections.commands.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>命令</th>
                    <th>描述</th>
                    <th>行为</th>
                    <th>远程</th>
                  </tr>
                </thead>
                <tbody>
                  {sections.commands.map((command) => (
                    <CommandRow key={command.name} command={command} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      <p className="muted">
        此页只读，不会启动 MCP 服务器、模型或工具，也不会修改项目状态。生成时间{" "}
        {shortTime(view.generated_at)} · 命令行等价物：`ih capabilities`（`--json` / `--strict`
        供 CI 使用）。
      </p>
    </>
  );
}
