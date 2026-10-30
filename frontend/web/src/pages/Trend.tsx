// Trend (design §17.3 P8) — what each finished run added, fixed, or
// regressed, plus the findings that keep surviving across runs. Read-only:
// the backend derives it from the decision log and the trend library, so the
// page never mutates `.iterate` state and is safe to leave open.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TrendExportFormat } from "../api";
import { downloadTextFile } from "../download";
import { useWebUi } from "../store";
import {
  directionLabel,
  directionTone,
  netDirection,
  newestFirst,
  reportHasData,
  windowTotals,
} from "../trend";
import type { TrendFinding, TrendReport, TrendRun } from "../types";

const SEVERITY_LABELS: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

function severityLabel(severity: string): string {
  return SEVERITY_LABELS[severity.toLowerCase()] ?? severity;
}

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function Location({ finding }: { finding: TrendFinding }): React.JSX.Element {
  return (
    <span className="mono">
      {finding.file || "?"}
      {finding.line ? `:${finding.line}` : ""}
    </span>
  );
}

function FindingTable({
  findings,
  empty,
}: {
  findings: TrendFinding[];
  empty: string;
}): React.JSX.Element {
  if (findings.length === 0) return <p className="empty">{empty}</p>;
  return (
    <table className="data">
      <thead>
        <tr>
          <th>严重度</th>
          <th>位置</th>
          <th>维度</th>
          <th>摘要</th>
        </tr>
      </thead>
      <tbody>
        {findings.map((finding, index) => (
          <tr key={`${finding.fingerprint ?? finding.file}-${index}`}>
            <td>
              <span className={`badge severity-${finding.severity.toLowerCase()}`}>
                {severityLabel(finding.severity)}
              </span>
            </td>
            <td>
              <Location finding={finding} />
            </td>
            <td>{finding.dimension || "—"}</td>
            <td>{finding.summary || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DeltaColumn({
  title,
  tone,
  findings,
  empty,
}: {
  title: string;
  tone: string;
  findings: TrendFinding[];
  empty: string;
}): React.JSX.Element {
  return (
    <div className="trend-column">
      <h3>
        {title} <span className={`badge ${tone}`}>{findings.length}</span>
      </h3>
      {findings.length === 0 ? (
        <p className="empty small">{empty}</p>
      ) : (
        <ul className="trend-list">
          {findings.map((finding, index) => (
            <li key={`${finding.fingerprint ?? finding.file}-${index}`}>
              <span className={`badge severity-${finding.severity.toLowerCase()}`}>
                {severityLabel(finding.severity)}
              </span>{" "}
              <Location finding={finding} /> <span className="muted">· {finding.dimension}</span>
              <div className="muted trend-list-summary">{finding.summary}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Trend(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const logRevision = useWebUi((state) => state.logRevision);
  const [report, setReport] = useState<TrendReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAllRuns, setShowAllRuns] = useState(false);
  const [exporting, setExporting] = useState<TrendExportFormat | null>(null);
  const pushToast = useWebUi((state) => state.pushToast);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.trend(projectRoot, 50, 100));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectRoot]);

  useEffect(() => {
    void load();
  }, [load]);

  // New decision-log entries arrive while a loop runs; refetch (debounced by
  // the store's own SSE batching) so the history row appears without a reload.
  useEffect(() => {
    if (logRevision > 0) void load();
  }, [logRevision, load]);

  const runs = report?.runs ?? [];
  const totals = useMemo(() => windowTotals(runs), [runs]);
  const direction = useMemo(() => netDirection(runs), [runs]);
  const visibleRuns = useMemo(
    () => (showAllRuns ? newestFirst(runs) : newestFirst(runs).slice(0, 10)),
    [runs, showAllRuns],
  );

  const canExport = reportHasData(report);

  // The export carries the same window the page is showing (top=50,
  // history=100), so a downloaded digest never disagrees with the screen.
  const exportTrend = async (format: TrendExportFormat): Promise<void> => {
    setExporting(format);
    try {
      const { filename, text } = await api.exportTrend(projectRoot, format, 50, 100);
      const mime =
        format === "json"
          ? "application/json"
          : format === "csv"
            ? "text/csv;charset=utf-8"
            : "text/markdown;charset=utf-8";
      downloadTextFile(filename, text, mime);
      pushToast("success", `已导出 ${filename}`);
    } catch (caught) {
      pushToast(
        "error",
        `导出失败：${caught instanceof Error ? caught.message : String(caught)}`,
      );
    } finally {
      setExporting(null);
    }
  };

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">趋势</h1>
        <p className="page-sub">
          跨运行的 new / fixed / regressed 变化与顽固问题 · 可导出 Markdown / CSV / JSON
        </p>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button
          className="btn"
          onClick={() => void exportTrend("md")}
          disabled={!canExport || exporting !== null}
          title="把这份趋势导出成可直接贴进 PR / 周报的 Markdown"
        >
          {exporting === "md" ? "导出中…" : "导出 Markdown"}
        </button>
        <button
          className="btn"
          onClick={() => void exportTrend("csv")}
          disabled={!canExport || exporting !== null}
          title="每次运行一行的 CSV，可直接进表格"
        >
          {exporting === "csv" ? "导出中…" : "导出 CSV"}
        </button>
        <button
          className="btn"
          onClick={() => void exportTrend("json")}
          disabled={!canExport || exporting !== null}
          title="与页面完全一致的 JSON（脚本消费）"
        >
          {exporting === "json" ? "导出中…" : "导出 JSON"}
        </button>
        <button className="btn" onClick={() => void load()} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>
    </div>
  );

  if (loading && !report) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 加载趋势数据…
        </div>
      </>
    );
  }

  if (error && !report) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">加载趋势失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!report || !reportHasData(report)) {
    return (
      <>
        {header}
        <section className="panel">
          <h2>还没有可比较的运行</h2>
          <p className="muted">
            每次完成的 <code>iterate</code> 循环向决策日志追加一条 <code>report</code> 记录；
            这里据此计算每一轮的 new / fixed / regressed。完成一次运行后再回来。
          </p>
          <pre className="cmd-hint">ih iterate run --rounds 3</pre>
          <p className="muted">
            只想看当前未解决/顽固问题而不跑循环：<code>ih iterate trend</code> 同样会读取
            <code> .iterate/trend-library.json</code>。
          </p>
        </section>
      </>
    );
  }

  const latest = report.latest;

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次数据）：{error}</p>}

      <div className="cards">
        <div className="card">
          <div className="k">已完成运行</div>
          <div className="v">{report.total_runs}</div>
        </div>
        <div className="card">
          <div className="k">跟踪中的问题</div>
          <div className="v">{report.tracked}</div>
        </div>
        <div className="card">
          <div className="k">当前未解决</div>
          <div className="v">{report.open}</div>
        </div>
        <div className="card">
          <div className="k">顽固问题（3+ 轮）</div>
          <div className="v">{report.stubborn}</div>
        </div>
        <div className="card">
          <div className="k">可见窗口方向</div>
          <div className={`v small direction-${directionTone(direction)}`}>
            {directionLabel(direction)}
          </div>
          <div className="muted card-note">
            {totals.runs} 次运行 · 新增 {totals.new} · 修复 {totals.fixed} · 回归 {totals.regressed}
          </div>
        </div>
      </div>

      {latest && (
        <section className="panel">
          <h2>
            最近一轮变化 · 第 {latest.index} 次运行 · {shortTime(latest.timestamp)} · 判定{" "}
            {latest.verdict}
          </h2>
          <p className="muted" style={{ marginTop: 0 }}>
            共 {latest.total} 个问题（报告声明 {latest.reported_total}）· 新增 {latest.new} ·
            修复 {latest.fixed} · 回归 {latest.regressed} · 与上轮相同 {latest.unchanged}
          </p>
          <div className="trend-columns">
            <DeltaColumn
              title="回归"
              tone="severity-critical"
              findings={latest.regressed_findings ?? []}
              empty="没有回归问题"
            />
            <DeltaColumn
              title="新增"
              tone="amber"
              findings={latest.new_findings ?? []}
              empty="没有新增问题"
            />
            <DeltaColumn
              title="修复"
              tone="green"
              findings={latest.fixed_findings ?? []}
              empty="没有修复记录"
            />
          </div>
        </section>
      )}

      <section className="panel">
        <h2>运行历史</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          回归 = 之前修复过、这次又出现的问题。列表按最新在前展示。
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
                <th>时间</th>
                <th>轮次</th>
                <th>总数</th>
                <th>新增</th>
                <th>修复</th>
                <th>回归</th>
                <th>持平</th>
                <th>判定</th>
              </tr>
            </thead>
            <tbody>
              {visibleRuns.map((run: TrendRun) => (
                <tr key={run.index} className={run.regressed > 0 ? "row-warn" : ""}>
                  <td className="mono">{run.index}</td>
                  <td className="mono">{shortTime(run.timestamp)}</td>
                  <td className="mono">{run.round}</td>
                  <td className="mono">{run.total}</td>
                  <td className="mono">{run.new}</td>
                  <td className="mono">{run.fixed}</td>
                  <td className="mono">
                    {run.regressed > 0 ? (
                      <span className="badge severity-critical">{run.regressed}</span>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td className="mono">{run.unchanged}</td>
                  <td>{run.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {runs.length > 10 && (
          <button className="btn" onClick={() => setShowAllRuns((value) => !value)}>
            {showAllRuns ? "只看最近 10 次" : `显示全部 ${runs.length} 次`}
          </button>
        )}
      </section>

      <section className="panel">
        <h2>顽固问题（连续 3+ 次运行仍未解决）</h2>
        {report.stubborn_findings.length === 0 ? (
          <p className="empty">
            暂无顽固问题。{report.open > 0 ? `当前 ${report.open} 个未解决问题尚未达到 3 次运行。` : ""}
          </p>
        ) : (
          <FindingTable findings={report.stubborn_findings} empty="暂无顽固问题" />
        )}
        {Object.keys(report.open_by_dimension).length > 0 && (
          <div className="chip-row">
            <span className="muted">未解决按维度：</span>
            {Object.entries(report.open_by_dimension)
              .sort((a, b) => b[1] - a[1])
              .map(([dimension, count]) => (
                <span className="chip" key={dimension}>
                  {dimension} <strong>{count}</strong>
                </span>
              ))}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>当前未解决问题</h2>
        <FindingTable findings={report.open_findings} empty="没有未解决的问题" />
      </section>
    </>
  );
}
