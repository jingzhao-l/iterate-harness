// Runs (design §17.3 P2) — trajectory-style per-round timeline of the
// decision log plus a findings table with severity/dimension filters and
// expandable diff blocks.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Finding, TimelineEntry } from "../types";

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

  const [findings, setFindings] = useState<Finding[]>([]);
  const [findingsTotal, setFindingsTotal] = useState(0);
  const [findingsLoading, setFindingsLoading] = useState(true);
  const [severityFilter, setSeverityFilter] = useState("");
  const [dimensionFilter, setDimensionFilter] = useState("");

  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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
        const entries = await api.timeline(undefined, undefined, typeFilter || undefined);
        if (!cancelled) setTimeline(entries);
      } catch (error) {
        if (!cancelled) {
          setTimeline([]);
          setTimelineError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) setTimelineLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [typeFilter]);

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
        <span className="muted">共 {findingsTotal} 个（去重）</span>
      </div>

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
              </tr>
            </thead>
            <tbody>
              {findings.map((finding, index) => (
                <tr key={index}>
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
