// Permission policy + decision simulator (design §17.3 P16).
// Before this page the layered policy (mode, allow/deny lists, path and
// command rules, sensitive paths, risk areas, forbidden patterns) was only
// observable as the outcome of a live tool call, and "would this be allowed?"
// required running the agent. The page shows the merged policy the engine
// enforces and runs the engine's own checker over a hypothetical call.
// Read-only: it never writes settings, executes a tool or contacts a provider.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  MODE_OPTIONS,
  PERMISSION_PRESETS,
  VERDICT_LABELS,
  permissionCheckCommand,
  permissionHeadline,
  pathRuleLabel,
  readOnlySourceLabel,
  verdictTone,
} from "../permissions";
import { useWebUi } from "../store";
import type {
  PermissionCheckInput,
  PermissionCheckView,
  PermissionProblem,
  PermissionsView,
} from "../types";

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
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
      onClick={() => {
        if (!navigator.clipboard?.writeText) {
          pushToast("error", "无法写入剪贴板，请手动选择文本");
          return;
        }
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => pushToast("error", "无法写入剪贴板，请手动选择文本"));
      }}
      disabled={!text}
    >
      {copied ? "已复制" : label}
    </button>
  );
}

function ProblemRow({ problem }: { problem: PermissionProblem }): React.JSX.Element {
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

function ChipList({
  values,
  empty,
  tone = "neutral",
}: {
  values: string[];
  empty: string;
  tone?: string;
}): React.JSX.Element {
  if (values.length === 0) return <span className="muted">{empty}</span>;
  return (
    <div className="chip-row">
      {values.map((value) => (
        <span key={value} className={`badge ${tone} mono`}>
          {value}
        </span>
      ))}
    </div>
  );
}

function ResultCard({
  result,
}: {
  result: PermissionCheckView;
}): React.JSX.Element {
  const command = permissionCheckCommand({
    tool: result.tool_name,
    path: result.file_path,
    command: result.command,
    mode: result.simulated_mode,
  });
  return (
    <div className={`permission-result verdict-${result.verdict}`}>
      <div className="permission-result-head">
        <span className={`badge ${verdictTone(result.verdict)}`}>
          {VERDICT_LABELS[result.verdict] ?? result.verdict}
        </span>
        <span className="mono strong">{result.tool_name}</span>
        <span className="muted">
          {result.mode_label || result.mode}模式
          {result.simulated_mode ? ` · 模拟 ${result.simulated_mode}` : ""}
        </span>
      </div>
      <p className="permission-reason">{result.reason}</p>
      <dl className="permission-facts">
        <div>
          <dt>只读判定</dt>
          <dd className="mono">
            {result.read_only ? "只读" : "可写"} ·{" "}
            {readOnlySourceLabel(result.read_only_source)}
          </dd>
        </div>
        {result.file_path && (
          <div>
            <dt>目标路径</dt>
            <dd className="mono permission-path" title={result.file_path}>
              {result.file_path}
            </dd>
          </div>
        )}
        {result.command && (
          <div>
            <dt>命令</dt>
            <dd className="mono permission-path" title={result.command}>
              {result.command}
            </dd>
          </div>
        )}
        {result.command_paths.length > 0 && (
          <div>
            <dt>命令中识别到的路径</dt>
            <dd className="mono permission-path">
              {result.command_paths.join(", ")}
            </dd>
          </div>
        )}
        {result.content_checked && (
          <div>
            <dt>写入内容</dt>
            <dd>已按 forbidden_fix_patterns 检查</dd>
          </div>
        )}
      </dl>
      <div className="remedy-row">
        <code className="remedy">{command}</code>
        <CopyButton text={command} label="复制等价命令" />
      </div>
    </div>
  );
}

export default function Permissions(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const pushToast = useWebUi((state) => state.pushToast);
  const [view, setView] = useState<PermissionsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [tool, setTool] = useState("read_file");
  const [mode, setMode] = useState("");
  const [path, setPath] = useState("");
  const [command, setCommand] = useState("");
  const [content, setContent] = useState("");
  const [readOnly, setReadOnly] = useState<"auto" | "true" | "false">("auto");
  const [result, setResult] = useState<PermissionCheckView | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.permissions(projectRoot));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      pushToast("error", `读取权限策略失败：${message}`);
    } finally {
      setLoading(false);
    }
  }, [projectRoot, pushToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const runCheck = useCallback(
    async (overrides: Partial<PermissionCheckInput> = {}): Promise<void> => {
      const nextTool = (overrides.tool ?? tool).trim();
      if (!nextTool) {
        setCheckError("请填写要测试的工具名");
        return;
      }
      const input: PermissionCheckInput = {
        tool: nextTool,
        projectRoot,
        path: overrides.path ?? path,
        command: overrides.command ?? command,
        content: overrides.content ?? content,
        mode: overrides.mode ?? mode,
      };
      const forced =
        overrides.readOnly !== undefined
          ? overrides.readOnly
          : readOnly === "auto"
            ? undefined
            : readOnly === "true";
      if (forced !== undefined) input.readOnly = forced;

      setChecking(true);
      setCheckError(null);
      try {
        setResult(await api.checkPermission(input));
      } catch (caught) {
        setResult(null);
        setCheckError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setChecking(false);
      }
    },
    [tool, path, command, content, mode, readOnly, projectRoot],
  );

  const applyPreset = (id: string): void => {
    const preset = PERMISSION_PRESETS.find((item) => item.id === id);
    if (!preset) return;
    setTool(preset.input.tool);
    setPath(preset.input.path ?? "");
    setCommand(preset.input.command ?? "");
    setContent(preset.input.content ?? "");
    setReadOnly("auto");
    setMode(preset.input.mode ?? "");
    void runCheck({
      ...preset.input,
      mode: preset.input.mode ?? "",
      readOnly: preset.input.readOnly,
    });
  };

  const headline = permissionHeadline(view);
  const tools = view?.tools ?? [];

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">权限</h1>
        <p className="page-sub">
          引擎实际执行的合并策略，以及一次工具调用的预演结果（与 `ih permissions` 同源）
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
          <span className="spinner" /> 加载权限策略…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取权限策略失败：{error}</p>
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
          <div className="k">路径规则</div>
          <div className="v small mono">{view.summary.path_rules ?? 0}</div>
          <div className="muted card-note">
            拒绝 {view.summary.deny_path_rules ?? 0} · 允许{" "}
            {view.summary.allow_path_rules ?? 0}
          </div>
        </div>
        <div className="card">
          <div className="k">风险区</div>
          <div className="v small mono">{view.summary.risk_areas ?? 0}</div>
          <div className="muted card-note">改动始终需确认</div>
        </div>
        <div className="card">
          <div className="k">内置敏感路径</div>
          <div className="v small mono">{view.summary.sensitive_paths ?? 0}</div>
          <div className="muted card-note">任何模式都拒绝</div>
        </div>
        <div className="card">
          <div className="k">工具</div>
          <div className="v small mono">{view.summary.tools ?? 0}</div>
          <div className="muted card-note">
            允许 {view.summary.allowed_tools ?? 0} · 拒绝{" "}
            {view.summary.denied_tools ?? 0}
          </div>
        </div>
      </div>

      <section className="panel">
        <h2>策略预演</h2>
        <p className="muted">
          用引擎同一个检查器回答「这条调用会直接执行、停下来问我，还是被拒绝，以及为什么」。
          不启动模型、不执行工具、不改配置；未知工具按可写处理（fail-closed）。
        </p>

        <div className="chip-row permission-presets">
          {PERMISSION_PRESETS.map((preset) => (
            <button
              key={preset.id}
              className="btn small"
              type="button"
              title={preset.description}
              onClick={() => applyPreset(preset.id)}
              disabled={checking}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <form
          className="permission-form"
          onSubmit={(event) => {
            event.preventDefault();
            void runCheck();
          }}
        >
          <label>
            工具
            <input
              type="text"
              list="permission-tools"
              value={tool}
              onChange={(event) => setTool(event.target.value)}
              placeholder="read_file / bash / write_file"
              autoComplete="off"
            />
          </label>
          <datalist id="permission-tools">
            {tools.map((item) => (
              <option key={item.name} value={item.name}>
                {item.read_only === true
                  ? "只读"
                  : item.read_only === false
                    ? "可写"
                    : "未知"}
              </option>
            ))}
          </datalist>
          <label>
            模式
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              {MODE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            只读判定
            <select
              value={readOnly}
              onChange={(event) =>
                setReadOnly(event.target.value as "auto" | "true" | "false")
              }
            >
              <option value="auto">按工具推断</option>
              <option value="true">强制只读</option>
              <option value="false">强制可写</option>
            </select>
          </label>
          <label>
            路径
            <input
              type="text"
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder=".env / src/app.py / db/migrations/001.sql"
              autoComplete="off"
            />
          </label>
          <label className="permission-wide">
            命令
            <input
              type="text"
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder="git push --force origin main"
              autoComplete="off"
            />
          </label>
          <label className="permission-wide">
            写入内容（可选，按 forbidden_fix_patterns 检查）
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={2}
              placeholder="只在工具可写时检查"
            />
          </label>
          <div className="permission-actions">
            <button className="btn primary" type="submit" disabled={checking}>
              {checking ? "预演中…" : "预演这次调用"}
            </button>
          </div>
        </form>

        {checkError && <p className="empty">预演失败：{checkError}</p>}
        {result ? (
          <ResultCard result={result} />
        ) : (
          !checkError && (
            <p className="empty">
              尚未预演。选择上面的示例或填写工具后点「预演这次调用」。
            </p>
          )
        )}
      </section>

      <section className="panel">
        <h2>配置问题</h2>
        <p className="muted">
          这些规则在运行时会静默失效：引擎跳过它并继续，只有日志里留一行。逐条修复后才算真正生效。
        </p>
        {view.problems.length === 0 ? (
          <p className="empty">未发现永远无法生效的规则。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>类型</th>
                  <th>规则</th>
                  <th>问题</th>
                  <th>修复方法</th>
                </tr>
              </thead>
              <tbody>
                {view.problems.map((problem) => (
                  <ProblemRow
                    key={`${problem.kind}:${problem.name}`}
                    problem={problem}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>生效中的策略</h2>
        <p className="muted">
          读取时间 {shortTime(view.generated_at)} · 项目 {view.project_root} ·
          iterate 边界 {view.iterate_enabled ? "已启用" : "已停用"}
        </p>

        <h3>路径规则</h3>
        {view.path_rules.length === 0 ? (
          <p className="empty">没有自定义路径规则。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>动作</th>
                  <th>匹配模式</th>
                </tr>
              </thead>
              <tbody>
                {view.path_rules.map((rule) => (
                  <tr
                    key={`${rule.allow ? "allow" : "deny"}:${rule.pattern}`}
                    className={rule.allow ? "row-dim" : "row-warn"}
                  >
                    <td>
                      <span
                        className={`badge ${rule.allow ? "green" : "severity-high"}`}
                      >
                        {pathRuleLabel(rule)}
                      </span>
                    </td>
                    <td className="mono">{rule.pattern}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3>工具</h3>
        <p className="muted">允许列表</p>
        <ChipList values={view.allowed_tools} empty="（无，按模式逻辑处理）" tone="green" />
        <p className="muted">拒绝列表</p>
        <ChipList values={view.denied_tools} empty="（无）" tone="severity-high" />

        <h3>命令</h3>
        <p className="muted">拒绝模式（先于允许列表）</p>
        <ChipList values={view.denied_commands} empty="（无）" tone="severity-high" />
        <p className="muted">精确允许列表</p>
        <ChipList values={view.allowed_commands} empty="（无）" tone="green" />

        <h3>风险区（改动需确认）</h3>
        <ChipList values={view.risk_areas} empty="（无）" tone="amber" />

        <h3>禁止写入内容（正则）</h3>
        <ChipList values={view.forbidden_patterns} empty="（无）" tone="severity-high" />

        <details className="permission-sensitive">
          <summary>内置敏感路径（{view.sensitive_paths.length}，始终拒绝）</summary>
          <ChipList values={view.sensitive_paths} empty="（无）" tone="neutral" />
        </details>
      </section>

      <p className="muted">
        此页只读：不写设置、不执行工具、不启动模型。策略来自引擎实际构建的检查器，
        与运行时判断一致。
      </p>
    </>
  );
}
