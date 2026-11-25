// Defensive kernel (design §17.3 P17) — what guards code-mode edits, what it
// has rolled back, and which declared assumptions were falsified. Before this
// page the guard existed only as a tool error inside a run: the effective set
// (invariants / validation fallback / explicit disable), an entry that can
// never run, and the reason a specific edit was reverted were not observable.
// Read-only: this page never executes invariant commands or contacts a model;
// it offers copyable `ih defense check` / `ih defense journal` commands.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadDefenseJournalExport } from "../api";
import {
  ASSUMPTION_STATUS_LABELS,
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
import { useWebUi } from "../store";
import type { DefenseExportFormat } from "../api";
import type { DefenseEvent, DefenseJournalView, DefenseView } from "../types";

const PAGE_SIZE = 50;

type Tab = "guard" | "journal";

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
      title={text}
      onClick={() => {
        if (!navigator.clipboard?.writeText) {
          pushToast("info", text);
          return;
        }
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => pushToast("info", text));
      }}
    >
      {copied ? "已复制" : label}
    </button>
  );
}

function ExportButton({
  format,
  label,
  projectRoot,
  query,
}: {
  format: DefenseExportFormat;
  label: string;
  projectRoot: string;
  query: { kind?: string; outcome?: string; tool?: string };
}): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const pushToast = useWebUi((state) => state.pushToast);

  const download = async (): Promise<void> => {
    setBusy(true);
    try {
      const result = await downloadDefenseJournalExport(projectRoot, format, query);
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

function EventRow({ event }: { event: DefenseEvent }): React.JSX.Element {
  const hasDetail = Boolean(event.reason) || event.violations.length > 0;
  return (
    <tr className={event.rolled_back ? "row-warn" : undefined}>
      <td className="mono muted">{shortTimestamp(event.timestamp)}</td>
      <td>
        <span className="badge neutral">{kindLabel(event.kind)}</span>
      </td>
      <td>
        <span className={`badge ${outcomeTone(event.outcome, event.rolled_back)}`}>
          {eventResultLabel(event)}
        </span>
      </td>
      <td className="mono">{event.tool || "—"}</td>
      <td className="mono permission-path" title={event.path}>
        {event.path || "—"}
      </td>
      <td className="mono">{event.checks_run}</td>
      <td>
        {hasDetail ? (
          <details>
            <summary className="muted">
              {event.violations.length > 0
                ? `${event.violations.length} 条违规`
                : "详情"}
            </summary>
            {event.reason && <div className="mono muted">{event.reason}</div>}
            {event.violations.map((violation, index) => (
              <div className="mono" key={`${violation.label}-${index}`}>
                [{violation.kind}] {violation.label}: {violation.detail}
              </div>
            ))}
          </details>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="mono muted">{event.duration_ms}ms</td>
    </tr>
  );
}

export default function Defense(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const [tab, setTab] = useState<Tab>("guard");

  const [view, setView] = useState<DefenseView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [log, setLog] = useState<DefenseJournalView | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [offset, setOffset] = useState(0);

  const loadInventory = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.defense(projectRoot));
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
      setLog(
        await api.defenseJournal(projectRoot, {
          kind: kindFilter,
          outcome: outcomeFilter,
          tool: toolFilter,
          offset,
          limit: PAGE_SIZE,
        }),
      );
    } catch (caught) {
      setLogError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLogLoading(false);
    }
  }, [projectRoot, kindFilter, outcomeFilter, toolFilter, offset]);

  useEffect(() => {
    void loadInventory();
  }, [loadInventory]);

  useEffect(() => {
    if (tab === "journal") void loadLog();
  }, [tab, loadLog]);

  const headline = useMemo(() => defenseHeadline(view), [view]);
  const checkCommand = useMemo(() => defenseCheckCommand(projectRoot), [projectRoot]);
  const totalRollbacks = view?.summary.rollbacks ?? 0;
  const lastVerdict = view?.summary.last_verdict ?? "";

  const kinds = useMemo(() => {
    const values = new Set(view?.journal_kinds ?? []);
    if (kindFilter) values.add(kindFilter);
    return Array.from(values).sort();
  }, [view, kindFilter]);
  const outcomes = useMemo(() => {
    const values = new Set(view?.journal_outcomes ?? []);
    if (outcomeFilter) values.add(outcomeFilter);
    return Array.from(values).sort();
  }, [view, outcomeFilter]);
  const tools = useMemo(() => {
    const values = new Set(view?.journal_tools ?? []);
    if (toolFilter) values.add(toolFilter);
    return Array.from(values).sort();
  }, [view, toolFilter]);

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">防御守卫</h1>
        <p className="page-sub">
          代码模式下，引擎在每次改动前快照、改动后重跑项目不变式；不通过就回滚。
          本页显示实际生效的守卫、被证伪的假设与每一次回滚。
          只读：本页不执行任何不变式命令，也不调用模型。
        </p>
      </div>

      <div className="chip-row">
        <button
          className={`chip ${tab === "guard" ? "active" : ""}`}
          onClick={() => setTab("guard")}
        >
          守卫（{view?.policy.checks ?? 0} 检查）
        </button>
        <button className={`chip ${tab === "journal" ? "active" : ""}`} onClick={() => setTab("journal")}>
          回滚台账{log ? `（${log.total}）` : ""}
        </button>
      </div>

      {tab === "guard" && (
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
                  <span className="k">状态</span>
                  <span className="v" style={{ fontSize: "1rem" }}>{headline.label}</span>
                </div>
                <div className="card">
                  <span className="k">自动回滚</span>
                  <span className="v">{totalRollbacks}</span>
                </div>
                <div className="card">
                  <span className="k">最近判定</span>
                  <span className="v" style={{ fontSize: "1rem" }}>
                    {lastVerdict ? outcomeLabel(lastVerdict) : "尚无记录"}
                  </span>
                </div>
                <div className="card">
                  <span className="k">假设（证伪）</span>
                  <span className="v">
                    {view.assumption_summary.total} / {view.assumption_summary.falsified}
                  </span>
                </div>
              </div>

              <section className="panel">
                <h2>
                  守卫来源：<code>{view.policy.source_label}</code>
                  <CopyButton text={checkCommand} label="复制检查命令" />
                </h2>
                <p className="muted">{headline.detail}</p>
                {view.policy.notes.map((note) => (
                  <p className="muted" key={note}>
                    · {note}
                  </p>
                ))}
                {view.policy.ensure.length > 0 && (
                  <>
                    <h3>文件断言（ensure）</h3>
                    <div className="chip-row">
                      {view.policy.ensure.map((entry, index) => (
                        <span className="badge neutral mono" key={`${entry}-${index}`}>
                          {entry || "(empty)"}
                        </span>
                      ))}
                    </div>
                  </>
                )}
                {Object.keys(view.policy.commands).length > 0 && (
                  <>
                    <h3>命令检查（commands）</h3>
                    <div className="table-wrap">
                      <table className="data">
                        <thead>
                          <tr>
                            <th>模块</th>
                            <th>命令</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(view.policy.commands).flatMap(([module, commands]) =>
                            commands.length === 0 ? (
                              <tr key={`${module}-empty`}>
                                <td className="mono">{module}</td>
                                <td className="muted">（空列表，不作为检查）</td>
                              </tr>
                            ) : (
                              commands.map((command, index) => (
                                <tr key={`${module}-${command}-${index}`}>
                                  <td className="mono">{module}</td>
                                  <td className="mono">{command}</td>
                                </tr>
                              ))
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
                {view.policy.checks === 0 && (
                  <div className="empty">
                    <p>没有可运行的检查：守卫不会在编辑后做任何校验。</p>
                    <p className="muted">
                      在 <code>iterate.config.yaml</code> 的 <code>invariants.ensure</code> /{" "}
                      <code>invariants.commands</code> 中登记，或使用{" "}
                      <code>validation.commands</code> 回退；然后用{" "}
                      <code>ih defense check</code> 验证。
                    </p>
                  </div>
                )}
              </section>

              {view.has_problems && view.problems.length > 0 && (
                <section className="panel">
                  <h2>配置问题（{view.problems.length}）</h2>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>类别</th>
                          <th>条目</th>
                          <th>原因</th>
                          <th>修复</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.problems.map((problem, index) => (
                          <tr className="row-warn" key={`${problem.kind}-${problem.name}-${index}`}>
                            <td>
                              <span className="badge severity-medium">{problem.kind}</span>
                            </td>
                            <td className="mono strong">{problem.name}</td>
                            <td>{problem.detail}</td>
                            <td>
                              <div className="remedy-row">
                                <code className="remedy">{problem.remedy}</code>
                                <CopyButton text={problem.remedy} label="复制修复方法" />
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <section className="panel">
                <h2>假设（{view.assumption_summary.total}）</h2>
                {view.assumptions.length === 0 ? (
                  <div className="empty">
                    <p>尚未记录任何假设。</p>
                    <p className="muted">
                      只有 code 模式会要求模型在工具调用前声明假设；声明与验证结果写入决策日志。
                    </p>
                  </div>
                ) : (
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>状态</th>
                          <th>假设</th>
                          <th>证据 / 详情</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.assumptions.map((assumption) => (
                          <tr
                            key={`${assumption.statement}-${assumption.timestamp}`}
                            className={assumption.status === "falsified" ? "row-warn" : undefined}
                          >
                            <td className="mono muted">{shortTimestamp(assumption.timestamp)}</td>
                            <td>
                              <span className={`badge ${assumptionTone(assumption.status)}`}>
                                {assumptionStatusLabel(assumption.status)}
                              </span>
                            </td>
                            <td>{assumption.statement}</td>
                            <td className="muted">{assumption.detail || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="muted">
                  状态图例：{Object.values(ASSUMPTION_STATUS_LABELS).join(" / ")}。
                  已证伪的假设应视为本轮停止的理由，而不是一条普通日志。
                </p>
              </section>
            </>
          )}
        </>
      )}

      {tab === "journal" && (
        <>
          <section className="panel">
            <h2>筛选与导出</h2>
            <div className="filter-row">
              <label>
                类型
                <select
                  value={kindFilter}
                  onChange={(event) => {
                    setKindFilter(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">全部</option>
                  {kinds.map((value) => (
                    <option value={value} key={value}>
                      {kindLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                结果
                <select
                  value={outcomeFilter}
                  onChange={(event) => {
                    setOutcomeFilter(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">全部</option>
                  {outcomes.map((value) => (
                    <option value={value} key={value}>
                      {outcomeLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                工具
                <select
                  value={toolFilter}
                  onChange={(event) => {
                    setToolFilter(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">全部</option>
                  {tools.map((value) => (
                    <option value={value} key={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <button className="btn small" onClick={() => void loadLog()} disabled={logLoading}>
                {logLoading ? "刷新中…" : "刷新"}
              </button>
              <CopyButton
                text={defenseJournalCommand(projectRoot, {
                  kind: kindFilter,
                  outcome: outcomeFilter,
                  tool: toolFilter,
                })}
                label="复制导出命令"
              />
            </div>
            <div className="chip-row">
              <ExportButton
                format="md"
                label="导出 Markdown"
                projectRoot={projectRoot}
                query={{ kind: kindFilter, outcome: outcomeFilter, tool: toolFilter }}
              />
              <ExportButton
                format="csv"
                label="导出 CSV"
                projectRoot={projectRoot}
                query={{ kind: kindFilter, outcome: outcomeFilter, tool: toolFilter }}
              />
              <ExportButton
                format="json"
                label="导出 JSON"
                projectRoot={projectRoot}
                query={{ kind: kindFilter, outcome: outcomeFilter, tool: toolFilter }}
              />
            </div>
          </section>

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
            <section className="panel">
              <h2>
                回滚台账 <span className="muted">（{log.total}）</span>
              </h2>
              {log.entries.length === 0 ? (
                <div className="empty">
                  <p>{log.total === 0 ? "尚无防御事件。" : "当前筛选没有匹配的事件。"}</p>
                  <p className="muted">
                    引擎在每次守卫判定和回滚时写入{" "}
                    <code>.iterate/defense-journal.jsonl</code>；先跑一轮 code 模式或执行{" "}
                    <code>ih defense check</code>。
                  </p>
                </div>
              ) : (
                <>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>类型</th>
                          <th>结果</th>
                          <th>工具</th>
                          <th>路径</th>
                          <th>检查数</th>
                          <th>原因 / 违规</th>
                          <th>耗时</th>
                        </tr>
                      </thead>
                      <tbody>
                        {log.entries.map((event) => (
                          <EventRow event={event} key={event.id || `${event.timestamp}-${event.tool}`} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="pager">
                    <button
                      className="btn small"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    >
                      上一页
                    </button>
                    <span className="muted">
                      显示 {offset + 1}–{offset + log.returned} / {log.total}
                    </span>
                    <button
                      className="btn small"
                      disabled={!log.truncated}
                      onClick={() => setOffset(offset + PAGE_SIZE)}
                    >
                      下一页
                    </button>
                  </div>
                </>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
