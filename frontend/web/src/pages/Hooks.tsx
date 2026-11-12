// Lifecycle hooks (design §17.3 P15) — what the engine registers for each
// lifecycle event (settings + enabled plugins) and the execution trail the
// engine records for every hook it actually runs. Before this page an operator
// could not see a hook at all: one registered under a misspelled event name was
// silently dropped, and one that rejected a tool call only surfaced as a
// mystery refusal inside a run. Read-only: no hook command is spawned and no
// model is invoked.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadHookLogExport } from "../api";
import {
  hookHeadline,
  hookRowStatus,
  hookTestCommand,
  hookTypeClass,
  statusLabel,
} from "../hooks";
import { useWebUi } from "../store";
import type { HookExportFormat } from "../api";
import type { HookLogView, HooksView } from "../types";

const PAGE_SIZE = 50;

type Tab = "registered" | "log";

function HookTypeBadge({ type }: { type: string }): React.JSX.Element {
  const safe = type || "unknown";
  return <span className={`badge ${hookTypeClass(safe)}`}>{safe}</span>;
}

function CopyTestCommand({ event }: { event: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  const pushToast = useWebUi((state) => state.pushToast);
  const command = hookTestCommand(event);
  const copy = (): void => {
    const clipboard = navigator.clipboard;
    if (!clipboard) {
      // No Clipboard API (insecure origin / older browser): show the command
      // instead of a button that silently does nothing.
      pushToast("info", command);
      return;
    }
    void clipboard
      .writeText(command)
      .then(() => {
        setCopied(true);
        pushToast("success", "已复制试运行命令");
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        pushToast("info", command);
      });
  };
  return (
    <button className="btn small" onClick={copy} title={command}>
      {copied ? "已复制" : "复制试运行命令"}
    </button>
  );
}

function HookExportButton({
  format,
  label,
  projectRoot,
  query,
}: {
  format: HookExportFormat;
  label: string;
  projectRoot: string;
  query: { event?: string; hookType?: string; success?: string };
}): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const pushToast = useWebUi((state) => state.pushToast);

  const download = async (): Promise<void> => {
    setBusy(true);
    try {
      const result = await downloadHookLogExport(projectRoot, format, query);
      const url = URL.createObjectURL(new Blob([result.text], { type: result.contentType }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      pushToast("success", `已导出 ${result.filename}`);
    } catch (caught) {
      pushToast("error", caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <button className="btn small" onClick={() => void download()} disabled={busy}>
      {busy ? "导出中…" : label}
    </button>
  );
}

export default function Hooks(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const [tab, setTab] = useState<Tab>("registered");

  // ---- registered inventory ----
  const [view, setView] = useState<HooksView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ---- execution log ----
  const [log, setLog] = useState<HookLogView | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);
  const [eventFilter, setEventFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [successFilter, setSuccessFilter] = useState("");
  const [offset, setOffset] = useState(0);

  const loadInventory = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.hooks(projectRoot);
      setView(data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectRoot]);

  const loadLog = useCallback(async (): Promise<void> => {
    setLogLoading(true);
    setLogError(null);
    try {
      const data = await api.hookLog(projectRoot, {
        event: eventFilter,
        hookType: typeFilter,
        success: successFilter,
        offset,
        limit: PAGE_SIZE,
      });
      setLog(data);
    } catch (caught) {
      setLogError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLogLoading(false);
    }
  }, [projectRoot, eventFilter, typeFilter, successFilter, offset]);

  useEffect(() => {
    void loadInventory();
  }, [loadInventory]);

  useEffect(() => {
    if (tab === "log") void loadLog();
  }, [tab, loadLog]);

  const totalHooks = useMemo(() => view?.summary?.total ?? 0, [view]);
  const headline = useMemo(() => (view ? hookHeadline(view) : null), [view]);

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">生命周期钩子</h1>
        <p className="page-sub">
          引擎从 settings.hooks 与已启用插件加载这些钩子，并在工具调用 / 回合边界内联执行。
          只读：本页不会启动任何钩子命令或模型。
        </p>
      </div>

      <div className="chip-row">
        <button
          className={`chip ${tab === "registered" ? "active" : ""}`}
          onClick={() => setTab("registered")}
        >
          已注册（{totalHooks}）
        </button>
        <button className={`chip ${tab === "log" ? "active" : ""}`} onClick={() => setTab("log")}>
          执行记录{log ? `（${log.total}）` : ""}
        </button>
      </div>

      {tab === "registered" && (
        <>
          {loading && <div className="loading-block">加载中…</div>}
          {!loading && error && (
            <div className="row-warn">
              <p>读取失败：{error}</p>
              <button className="btn small" onClick={() => void loadInventory()}>
                重试
              </button>
            </div>
          )}
          {!loading && !error && view && (
            <>
              <div className="cards">
                <div className="card">
                  <span className="k">已注册钩子</span>
                  <span className="v">{headline?.total ?? 0}</span>
                </div>
                <div className="card">
                  <span className="k">事件</span>
                  <span className="v">{headline?.events ?? 0}</span>
                </div>
                <div className="card">
                  <span className="k">已执行</span>
                  <span className="v">{headline?.executed ?? 0}</span>
                </div>
                <div className="card">
                  <span className="k">失败 / 拦截</span>
                  <span className="v">
                    {headline?.failures ?? 0} / {headline?.blocked ?? 0}
                  </span>
                </div>
              </div>

              {view.has_problems && view.problems.length > 0 && (
                <section className="panel">
                  <h2>问题（{view.problems.length}）</h2>
                  {view.problems.map((problem, index) => (
                    <div className="remedy-row" key={`${problem.kind}-${problem.name}-${index}`}>
                      <span className="badge warn">[{problem.kind}]</span> {problem.name}: {problem.detail}
                      {problem.remedy && <div className="remedy mono">修复：{problem.remedy}</div>}
                    </div>
                  ))}
                </section>
              )}

              {view.events.length === 0 ? (
                <div className="empty">
                  <p>尚未注册任何生命周期钩子。</p>
                  <p className="muted">
                    在 iterate.config.yaml 的 <code>hooks:</code> 段或插件中登记后，本页会列出它们。
                  </p>
                </div>
              ) : (
                view.events.map((group) => (
                  <section className="panel" key={group.event}>
                    <h2>
                      <code>{group.event}</code> <span className="muted">（{group.count}）</span>
                      <CopyTestCommand event={group.event} />
                    </h2>
                    <div className="table-wrap">
                      <table className="data">
                        <thead>
                          <tr>
                            <th>类型</th>
                            <th>来源</th>
                            <th>匹配</th>
                            <th>命令 / 提示 / URL</th>
                            <th>超时</th>
                            <th>失败拦截</th>
                            <th>检查</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.hooks.map((hook, index) => {
                            const check = hookRowStatus(hook.issues);
                            return (
                              <tr key={`${group.event}-${hook.source}-${index}`}>
                                <td>
                                  <HookTypeBadge type={hook.type} />
                                </td>
                                <td className="mono">{hook.source}</td>
                                <td className="mono">{hook.matcher || "—"}</td>
                                <td className="mono">
                                  {hook.detail || "—"}
                                  {(hook.issues ?? []).map((issue) => (
                                    <div className="remedy" key={issue}>
                                      {issue}
                                    </div>
                                  ))}
                                </td>
                                <td className="mono">{hook.timeout_seconds}s</td>
                                <td>{hook.block_on_failure ? "是" : "否"}</td>
                                <td>
                                  <span className={`badge ${check.cls}`}>{check.text}</span>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </section>
                ))
              )}
            </>
          )}
        </>
      )}

      {tab === "log" && (
        <>
          <div className="filter-row hooks-filters">
            <select
              aria-label="按事件筛选"
              value={eventFilter}
              onChange={(e) => {
                setEventFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部事件</option>
              {(log?.events ?? []).map((event) => (
                <option key={event} value={event}>
                  {event}
                </option>
              ))}
            </select>
            <select
              aria-label="按类型筛选"
              value={typeFilter}
              onChange={(e) => {
                setTypeFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部类型</option>
              {(log?.types ?? []).map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
            <select
              aria-label="按结果筛选"
              value={successFilter}
              onChange={(e) => {
                setSuccessFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部结果</option>
              <option value="true">仅通过</option>
              <option value="false">仅失败</option>
            </select>
            <button className="btn small" onClick={() => void loadLog()} disabled={logLoading}>
              {logLoading ? "刷新中…" : "刷新"}
            </button>
            <HookExportButton
              format="md"
              label="导出 Markdown"
              projectRoot={projectRoot}
              query={{ event: eventFilter, hookType: typeFilter, success: successFilter }}
            />
            <HookExportButton
              format="csv"
              label="导出 CSV"
              projectRoot={projectRoot}
              query={{ event: eventFilter, hookType: typeFilter, success: successFilter }}
            />
            <HookExportButton
              format="json"
              label="导出 JSON"
              projectRoot={projectRoot}
              query={{ event: eventFilter, hookType: typeFilter, success: successFilter }}
            />
          </div>

          {logLoading && <div className="loading-block">加载中…</div>}
          {!logLoading && logError && (
            <div className="row-warn">
              <p>读取失败：{logError}</p>
              <button className="btn small" onClick={() => void loadLog()}>
                重试
              </button>
            </div>
          )}
          {!logLoading && !logError && log && (
            <>
              {log.entries.length === 0 ? (
                <div className="empty">
                  <p>{log.total === 0 ? "尚未记录任何钩子执行。" : "当前筛选无匹配记录。"}</p>
                  <p className="muted">
                    引擎每次运行一个钩子都会写入 .iterate/hook-journal.jsonl；这里按时间倒序分页。
                  </p>
                </div>
              ) : (
                <>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>事件</th>
                          <th>类型</th>
                          <th>结果</th>
                          <th>耗时</th>
                          <th>详情</th>
                        </tr>
                      </thead>
                      <tbody>
                        {log.entries.map((entry, index) => {
                          const status = statusLabel(entry);
                          return (
                            <tr key={`${entry.timestamp}-${index}`}>
                              <td className="mono" style={{ whiteSpace: "nowrap" }}>
                                {entry.timestamp.slice(0, 19)}
                              </td>
                              <td className="mono">{entry.event}</td>
                              <td>
                                <HookTypeBadge type={entry.hook_type} />
                              </td>
                              <td>
                                <span className={`badge ${status.cls}`}>{status.text}</span>
                              </td>
                              <td className="mono">{entry.duration_ms}ms</td>
                              <td className="mono">
                                {entry.detail || "—"}
                                {(entry.reason || entry.output) && (
                                  <details>
                                    <summary>结果</summary>
                                    <pre>{entry.reason || entry.output}</pre>
                                  </details>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="filter-row">
                    <span className="muted">
                      显示 {log.offset + log.entries.length} / {log.total} 条
                    </span>
                    <button
                      className="btn small"
                      disabled={offset === 0}
                      onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
                    >
                      上一页
                    </button>
                    <button
                      className="btn small"
                      disabled={!log.truncated}
                      onClick={() => setOffset((value) => value + PAGE_SIZE)}
                    >
                      下一页
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
