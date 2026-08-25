// Runs (design §17.3 P2) — trajectory-style per-round timeline of the
// decision log plus a findings table with severity/dimension filters and
// expandable diff blocks.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useWebUi } from "../store";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Finding, TimelineEntry, TriageDecision } from "../types";

// Compose the triage dedup key exactly like the backend (file:::line:::dimension).
// Exported for unit tests (design §17.9 quality gates).
export function triageKey(finding: Finding): string {
  return `${finding.file ?? ""}:::${finding.line ?? ""}:::${finding.dimension ?? ""}`;
}

const ENTRY_TYPE_LABELS: Record<string, string> = {
  round_start: "轮次开始",
  review_result: "评审结果",
  atomic_fix: "原子修复",
  architectural_fix: "架构修复",
  revert: "回滚",
  validation: "验证",
  decision: "决策",
  report: "报告",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

// Findings table rows per page — the backend returns the full findings list,
// so we cap the rendered rows to keep long runs fluid (frontend pagination).
const FINDINGS_PAGE_SIZE = 50;

function asString(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return String(value);
}

// The decision log stores `diff` as a unified-diff string (see html_report.py).
// Normalize both a string and a pre-split array into lines so the expandable
// diff block renders regardless of the exact persisted shape.
function diffLines(diff: unknown): string[] {
  if (Array.isArray(diff)) return diff.map((line) => String(line));
  if (typeof diff === "string" && diff.trim()) return diff.split("\n");
  return [];
}

function severityColor(severity: string): string {
  return severity in SEVERITY_LABELS ? `severity-${severity}` : "neutral";
}

function EntryBody({ entry }: { entry: TimelineEntry }): React.JSX.Element {
  const data = entry.data ?? {};
  const roundLabel = entry.round > 0 ? `R${entry.round}` : "—";

  if (entry.type === "review_result" || entry.type === "report") {
    const findings = Array.isArray(data.findings) ? (data.findings as Finding[]) : [];
    const severityCounts: Record<string, number> = {};
    for (const finding of findings) {
      const severity = asString(finding.severity).toLowerCase() || "medium";
      severityCounts[severity] = (severityCounts[severity] ?? 0) + 1;
    }
    return (
      <div>
        <span className="tag">{roundLabel}</span>
        {data.verdict ? (
          <span>
            判定：<strong>{asString(data.verdict)}</strong> ·{" "}
          </span>
        ) : null}
        {findings.length > 0 ? (
          <span>
            共 {findings.length} 个 findings
            {Object.entries(severityCounts).map(([severity, count]) => (
              <span key={severity} style={{ marginLeft: 10 }}>
                <span className={`badge ${severityColor(severity)}`}>
                  {SEVERITY_LABELS[severity] ?? severity}
                </span>{" "}
                {String(count)}
              </span>
            ))}
          </span>
        ) : (
          <span className="muted">无 findings</span>
        )}
        {data.summary ? <p className="muted">{asString(data.summary)}</p> : null}
      </div>
    );
  }

  if (entry.type === "atomic_fix" || entry.type === "architectural_fix") {
    const file = asString(data.file);
    return (
      <div>
        <span className="tag">{roundLabel}</span>
        <span>
          {entry.type === "atomic_fix" ? "原子修复" : "架构修复"} ·{" "}
          <span className="mono">{file}</span>
        </span>
        {data.dimension ? <span className="muted"> · {asString(data.dimension)}</span> : null}
        {data.summary ? <p className="muted">{asString(data.summary)}</p> : null}
      </div>
    );
  }

  if (entry.type === "revert") {
    return (
      <div>
        <span className="tag">{roundLabel}</span>
        <span className="muted">{data.reason ? asString(data.reason) : "修复被回滚"}</span>
      </div>
    );
  }

  if (entry.type === "validation") {
    return (
      <div>
        <span className="tag">{roundLabel}</span>
        <span>
          {data.status ? <strong>{asString(data.status)}</strong> : "验证完成"}
          {data.command ? <span className="mono"> · {asString(data.command)}</span> : null}
        </span>
      </div>
    );
  }

  if (entry.type === "decision") {
    return (
      <div>
        <span className="tag">{roundLabel}</span>
        <span className="muted">{data.summary ? asString(data.summary) : "决策"}</span>
      </div>
    );
  }

  // round_start and any unknown type: show a generic summary line.
  return (
    <div>
      <span className="tag">{roundLabel}</span>
      <span className="muted">
        {data.summary ? asString(data.summary) : entry.type}
      </span>
    </div>
  );
}

export default function Runs(): React.JSX.Element {
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  // Bumped by the SSE stream whenever new decision-log entries arrive, so the
  // timeline + findings refetch while a loop is running (design §17 live data).
  const logRevision = useWebUi((state) => state.logRevision);
  const projectRoot = useWebUi((state) => state.projectRoot);

  // Timeline pagination: pages are counted from the newest entry (offset 0).
  // We request PAGE_SIZE+1 so an over-full response tells us older pages exist.
  const TIMELINE_PAGE_SIZE = 40;
  const [timelinePage, setTimelinePage] = useState(0);
  // Changing the type filter must start from the newest page again; otherwise
  // a user on page 2+ would land on the same stale offset of a filtered result
  // and see an empty page.
  useEffect(() => {
    setTimelinePage(0);
  }, [typeFilter]);
  const [timelineHasMore, setTimelineHasMore] = useState(false);
  // Retry nonces so an error state can trigger a clean refetch of each panel.
  const [timelineRetry, setTimelineRetry] = useState(0);
  const [findingsRetry, setFindingsRetry] = useState(0);

  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingsTotal, setFindingsTotal] = useState(0);
  const [findingsLoading, setFindingsLoading] = useState(true);
  const [findingsError, setFindingsError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState("");
  const [dimensionFilter, setDimensionFilter] = useState("");
  const [findingsPage, setFindingsPage] = useState(0);

  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Findings triage (design §17.3 P2): persisted approve/reject decisions.
  const [triage, setTriage] = useState<Record<string, TriageDecision>>({});
  const [triageBusyKey, setTriageBusyKey] = useState<string | null>(null);
  const [triageError, setTriageError] = useState<string | null>(null);
  const [showTriaged, setShowTriaged] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);

  // Changing any filter must jump back to the first findings page.
  useEffect(() => {
    setFindingsPage(0);
  }, [severityFilter, dimensionFilter, showTriaged]);

  // The SSE stream bumps logRevision on every decision-log append. Debounce it
  // so a burst of live events coalesces into one refetch instead of one per
  // event (which would re-render the whole findings table every second).
  const [debouncedRevision, setDebouncedRevision] = useState(logRevision);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedRevision(logRevision), 400);
    return () => clearTimeout(timer);
  }, [logRevision]);

  // Load persisted triage decisions once (reload when the project root changes).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const decisions = await api.triageDecisions(projectRoot);
        if (!cancelled) {
          const byKey: Record<string, TriageDecision> = {};
          for (const decision of decisions) byKey[decision.key] = decision;
          setTriage(byKey);
        }
      } catch {
        // Triaging is optional; a failed read just leaves all findings untriaged.
        if (!cancelled) setTriageError("无法读取审批记录");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectRoot]);

  const triageFinding = useCallback(
    (finding: Finding, decision: "approve" | "reject"): void => {
      const key = triageKey(finding);
      setTriageBusyKey(key);
      setTriageError(null);
      api
        .triageFinding(
          finding.file ?? "",
          finding.line ?? null,
          finding.dimension ?? "",
          decision,
          undefined,
          projectRoot,
        )
        .then((result) => {
          const record = result.detail?.record as TriageDecision | undefined;
          setTriage((prev) => ({ ...prev, [key]: record ?? {
            key,
            file: finding.file ?? "",
            line: finding.line ?? null,
            dimension: finding.dimension ?? "",
            decision,
            note: null,
            timestamp: new Date().toISOString(),
          }}));
        })
        .catch((error) => {
          setTriageError(error instanceof Error ? error.message : String(error));
        })
        .finally(() => setTriageBusyKey(null));
    },
    [projectRoot],
  );

  const clearTriage = useCallback((): void => {
    setClearBusy(true);
    setTriageError(null);
    api
      .clearTriage(projectRoot)
      .then(() => setTriage({}))
      .catch((error) => {
        setTriageError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        setConfirmClear(false);
        setClearBusy(false);
      });
  }, [projectRoot]);

  const triagedCount = Object.keys(triage).length;

  const dimensions = useMemo(() => {
    const seen = new Set<string>();
    for (const finding of findings) {
      const dimension = asString(finding.dimension);
      if (dimension !== "—") seen.add(dimension);
    }
    return [...seen].sort();
  }, [findings]);

  useEffect(() => {
    let cancelled = false;
    setTimelineLoading(true);
    setTimelineError(null);
    void (async () => {
      try {
        const page = await api.timeline(
          projectRoot,
          undefined,
          typeFilter || undefined,
          TIMELINE_PAGE_SIZE + 1,
          timelinePage * TIMELINE_PAGE_SIZE,
        );
        if (!cancelled) {
          setTimelineHasMore(page.length > TIMELINE_PAGE_SIZE);
          setTimeline(page.slice(0, TIMELINE_PAGE_SIZE));
        }
      } catch (error) {
        if (!cancelled) {
          setTimeline([]);
          setTimelineHasMore(false);
          setTimelineError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) setTimelineLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [typeFilter, timelinePage, debouncedRevision, timelineRetry, projectRoot]);

  useEffect(() => {
    let cancelled = false;
    setFindingsLoading(true);
    setFindingsError(null);
    void (async () => {
      try {
        const response = await api.findings(
          projectRoot,
          severityFilter || undefined,
          dimensionFilter || undefined,
        );
        if (!cancelled) {
          setFindings(response.findings);
          setFindingsTotal(response.total);
        }
      } catch (error) {
        if (!cancelled) {
          setFindings([]);
          setFindingsTotal(0);
          setFindingsError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) setFindingsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [severityFilter, dimensionFilter, debouncedRevision, findingsRetry, projectRoot]);

  const toggleExpanded = (index: number): void => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  // Apply the "only triaged" filter, then paginate the visible rows.
  const filteredFindings = findings.filter((finding) => {
    if (!showTriaged) return true;
    return triage[triageKey(finding)] !== undefined;
  });
  const findingsPageCount = Math.max(1, Math.ceil(filteredFindings.length / FINDINGS_PAGE_SIZE));
  const safeFindingsPage = Math.min(findingsPage, findingsPageCount - 1);
  const pageFindings = filteredFindings.slice(
    safeFindingsPage * FINDINGS_PAGE_SIZE,
    (safeFindingsPage + 1) * FINDINGS_PAGE_SIZE,
  );

  return (
    <>
      <h1 className="page-title">迭代详情</h1>
      <p className="page-sub">decision log 逐轮时间线与 findings 明细</p>

      <div className="filter-row">
        <label>
          时间线类型：
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">全部</option>
            {Object.entries(ENTRY_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section className="panel">
        <h2>轨迹时间线</h2>
        {timelineLoading ? (
          <div className="loading-block">
            <span className="spinner" /> 加载时间线…
          </div>
        ) : timelineError ? (
          <>
            <p className="empty">加载失败：{timelineError}</p>
            <div style={{ marginTop: 12 }}>
              <button
                className="btn primary"
                onClick={() => setTimelineRetry((n) => n + 1)}
              >
                重试
              </button>
            </div>
          </>
        ) : timeline.length === 0 ? (
          <p className="empty">暂无 decision log 条目</p>
        ) : (
          <>
            <div className="timeline">
              {timeline.map((entry) => (
                <div key={entry.index} className={`timeline-item ${entry.type}`}>
                  <div className="t">
                    {entry.timestamp} · R{entry.round} ·{" "}
                    {ENTRY_TYPE_LABELS[entry.type] ?? entry.type}
                  </div>
                  <div className="body">
                    <EntryBody entry={entry} />
                    {diffLines(entry.data?.diff).length > 0 && (
                      <div>
                        <button
                          className="btn"
                          style={{ marginTop: 6, padding: "4px 10px", fontSize: 12 }}
                          onClick={() => toggleExpanded(entry.index)}
                        >
                          {expanded.has(entry.index) ? "收起 diff" : "展开 diff"}
                        </button>
                        {expanded.has(entry.index) && (
                          <pre className="diff">{diffLines(entry.data?.diff).join("\n")}</pre>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div className="pager">
              <button
                className="btn"
                disabled={timelinePage === 0}
                onClick={() => setTimelinePage((p) => Math.max(0, p - 1))}
              >
                ← 更新的条目
              </button>
              <span className="muted">第 {timelinePage + 1} 页</span>
              <button
                className="btn"
                disabled={!timelineHasMore}
                onClick={() => setTimelinePage((p) => p + 1)}
              >
                更早的条目 →
              </button>
            </div>
          </>
        )}
      </section>

      <div className="filter-row">
        <label>
          严重度：
          <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
            <option value="">全部</option>
            <option value="critical">严重</option>
            <option value="high">高</option>
            <option value="medium">中</option>
            <option value="low">低</option>
          </select>
        </label>
        <label>
          维度：
          <select value={dimensionFilter} onChange={(event) => setDimensionFilter(event.target.value)}>
            <option value="">全部</option>
            {dimensions.map((dimension) => (
              <option key={dimension} value={dimension}>
                {dimension}
              </option>
            ))}
          </select>
        </label>
        <label>
          审批状态：
          <select
            value={showTriaged ? "triaged" : "all"}
            onChange={(event) => setShowTriaged(event.target.value === "triaged")}
          >
            <option value="all">全部</option>
            <option value="triaged">仅已审批</option>
          </select>
        </label>
        <span className="muted">
          共 {findingsTotal} 个（去重） · 已审批 {triagedCount} 个
        </span>
      </div>

      {triageError && <p className="form-error">{triageError}</p>}

      <section className="panel">
        <h2>Findings</h2>
        {findingsLoading ? (
          <div className="loading-block">
            <span className="spinner" /> 加载 findings…
          </div>
        ) : findingsError ? (
          <>
            <p className="empty">加载 findings 失败：{findingsError}</p>
            <div style={{ marginTop: 12 }}>
              <button
                className="btn primary"
                onClick={() => setFindingsRetry((n) => n + 1)}
              >
                重试
              </button>
            </div>
          </>
        ) : filteredFindings.length === 0 ? (
          <p className="empty">{showTriaged ? "暂无已审批的 findings" : "暂无 findings"}</p>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>严重度</th>
                <th>文件</th>
                <th>维度</th>
                <th>摘要</th>
                <th>修复建议</th>
                <th>审批</th>
              </tr>
            </thead>
            <tbody>
              {pageFindings.map((finding) => {
                const key = triageKey(finding);
                const decision = triage[key];
                const busy = triageBusyKey === key;
                return (
                  <tr key={key} className={decision ? `triage-${decision.decision}` : undefined}>
                      <td>
                        <span className={`badge ${severityColor(asString(finding.severity).toLowerCase())}`}>
                          {SEVERITY_LABELS[asString(finding.severity).toLowerCase()] ??
                            asString(finding.severity)}
                        </span>
                      </td>
                      <td className="mono">{asString(finding.file)}</td>
                      <td>{asString(finding.dimension)}</td>
                      <td>{asString(finding.summary)}</td>
                      <td className="muted">{asString(finding.suggested_fix)}</td>
                      <td>
                        <div className="triage-actions">
                          {decision ? (
                            <span
                              className={`badge ${decision.decision === "approve" ? "green" : "neutral"}`}
                              title={`${decision.decision === "approve" ? "已批准" : "已拒绝"} · ${decision.timestamp}`}
                            >
                              {decision.decision === "approve" ? "已批准" : "已拒绝"}
                            </span>
                          ) : (
                            <>
                              <button
                                className="btn"
                                style={{ padding: "3px 10px", fontSize: 12 }}
                                disabled={busy}
                                onClick={() => triageFinding(finding, "approve")}
                                title="同意该 finding / 接受其修复建议"
                              >
                                批准
                              </button>
                              <button
                                className="btn"
                                style={{ padding: "3px 10px", fontSize: 12 }}
                                disabled={busy}
                                onClick={() => triageFinding(finding, "reject")}
                                title="标记为误报 / 跳过其修复"
                              >
                                拒绝
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        )}
        {findingsPageCount > 1 && (
          <div className="pager">
            <button
              className="btn"
              disabled={safeFindingsPage === 0}
              onClick={() => setFindingsPage((page) => Math.max(0, page - 1))}
            >
              ← 上一页
            </button>
            <span className="muted">
              第 {safeFindingsPage + 1} / {findingsPageCount} 页 · 共{" "}
              {filteredFindings.length} 条
            </span>
            <button
              className="btn"
              disabled={safeFindingsPage >= findingsPageCount - 1}
              onClick={() => setFindingsPage((page) => page + 1)}
            >
              下一页 →
            </button>
          </div>
        )}
        {triagedCount > 0 && (
          <div className="pager" style={{ justifyContent: "flex-end" }}>
            <button
              className="btn"
              style={{ padding: "4px 10px", fontSize: 12 }}
              onClick={() => setConfirmClear(true)}
            >
              清除全部审批记录
            </button>
          </div>
        )}
      </section>

      {confirmClear && (
        <ConfirmDialog
          title="清除审批记录"
          confirmLabel="确认清除"
          danger
          busy={clearBusy}
          onCancel={() => setConfirmClear(false)}
          onConfirm={clearTriage}
        >
          将删除全部 {triagedCount} 条 findings 审批记录，此操作不可撤销。
        </ConfirmDialog>
      )}
    </>
  );
}
