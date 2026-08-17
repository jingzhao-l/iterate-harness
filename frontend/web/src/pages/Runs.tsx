// Runs (design §17.3 P2) — trajectory-style per-round timeline of the
// decision log plus a findings table with severity/dimension filters and
// expandable diff blocks.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Finding, TimelineEntry, TriageDecision } from "../types";

// Compose the triage dedup key exactly like the backend (file:::line:::dimension).
function triageKey(finding: Finding): string {
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

function asString(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return String(value);
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

  // Timeline pagination: pages are counted from the newest entry (offset 0).
  // We request PAGE_SIZE+1 so an over-full response tells us older pages exist.
  const TIMELINE_PAGE_SIZE = 40;
  const [timelinePage, setTimelinePage] = useState(0);
  const [timelineHasMore, setTimelineHasMore] = useState(false);

  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingsTotal, setFindingsTotal] = useState(0);
  const [findingsLoading, setFindingsLoading] = useState(true);
  const [severityFilter, setSeverityFilter] = useState("");
  const [dimensionFilter, setDimensionFilter] = useState("");

  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Findings triage (design §17.3 P2): persisted approve/reject decisions.
  const [triage, setTriage] = useState<Record<string, TriageDecision>>({});
  const [triageBusyKey, setTriageBusyKey] = useState<string | null>(null);
  const [triageError, setTriageError] = useState<string | null>(null);
  const [showTriaged, setShowTriaged] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  // Load persisted triage decisions once.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const decisions = await api.triageDecisions();
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
  }, []);

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
    [],
  );

  const clearTriage = useCallback((): void => {
    setTriageError(null);
    api
      .clearTriage()
      .then(() => setTriage({}))
      .catch((error) => {
        setTriageError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => setConfirmClear(false));
  }, []);

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
          undefined,
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
  }, [typeFilter, timelinePage]);

  useEffect(() => {
    let cancelled = false;
    setFindingsLoading(true);
    void (async () => {
      try {
        const response = await api.findings(
          undefined,
          severityFilter || undefined,
          dimensionFilter || undefined,
        );
        if (!cancelled) {
          setFindings(response.findings);
          setFindingsTotal(response.total);
        }
      } catch {
        if (!cancelled) {
          setFindings([]);
          setFindingsTotal(0);
        }
      } finally {
        if (!cancelled) setFindingsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [severityFilter, dimensionFilter]);

  const toggleExpanded = (index: number): void => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

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
          <p className="empty">加载失败：{timelineError}</p>
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
                    {Array.isArray(entry.data?.diff) && entry.data.diff.length > 0 && (
                      <div>
                        <button
                          className="btn"
                          style={{ marginTop: 6, padding: "4px 10px", fontSize: 12 }}
                          onClick={() => toggleExpanded(entry.index)}
                        >
                          {expanded.has(entry.index) ? "收起 diff" : "展开 diff"}
                        </button>
                        {expanded.has(entry.index) && (
                          <pre className="diff">
                            {(entry.data.diff as string[]).join("\n")}
                          </pre>
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
        ) : findings.length === 0 ? (
          <p className="empty">暂无 findings</p>
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
              {findings
                .filter((finding) => {
                  if (!showTriaged) return true;
                  return triage[triageKey(finding)] !== undefined;
                })
                .map((finding, index) => {
                  const key = triageKey(finding);
                  const decision = triage[key];
                  const busy = triageBusyKey === key;
                  return (
                    <tr key={index} className={decision ? `triage-${decision.decision}` : undefined}>
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
        <div className="modal-backdrop" onClick={() => setConfirmClear(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <h3>清除审批记录</h3>
            <p>将删除全部 {triagedCount} 条 findings 审批记录，此操作不可撤销。</p>
            <div className="actions">
              <button className="btn" onClick={() => setConfirmClear(false)}>
                取消
              </button>
              <button className="btn danger" onClick={clearTriage}>
                确认清除
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
