// Two-run comparison (design §17.3 P2) — "what changed between these two
// runs?". The engine could already classify findings by fingerprint
// (`trend_store.diff_runs`) but no surface ever called it: the trend page gave
// per-run counts and the latest run's findings, never the exact set that
// appeared, disappeared or came back between two chosen runs. Read-only.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  DIFF_SECTIONS,
  diffSummary,
  normalizePair,
  runOptionLabel,
} from "../diff";
import { useWebUi } from "../store";
import type { DiffFinding, RunDiffView } from "../types";

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

function FindingTable({ findings }: { findings: DiffFinding[] }): React.JSX.Element {
  return (
    <div className="table-wrap">
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
              <td className="mono">
                {finding.file || "?"}
                {finding.line ? `:${finding.line}` : ""}
              </td>
              <td>{finding.dimension || "—"}</td>
              <td title={finding.summary}>{finding.summary || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunCard({ label, run }: { label: string; run: RunDiffView["from_run"] }): React.JSX.Element {
  return (
    <div className="card">
      <div className="k">{label}</div>
      <div className="v small mono">{run ? `#${run.index}` : "—"}</div>
      <div className="muted card-note">
        {run
          ? `${shortTime(run.timestamp)} · ${run.verdict} · ${run.total} 条`
          : "还没有足够的运行"}
      </div>
    </div>
  );
}

export default function Diff(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const [view, setView] = useState<RunDiffView | null>(null);
  const [fromIndex, setFromIndex] = useState(0);
  const [toIndex, setToIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (from = 0, to = 0): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const next = await api.runDiff(projectRoot, from, to);
        setView(next);
        if (next.from_run && next.to_run) {
          setFromIndex(next.from_run.index);
          setToIndex(next.to_run.index);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setLoading(false);
      }
    },
    [projectRoot],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const choose = (nextFrom: number, nextTo: number): void => {
    const pair = normalizePair(nextFrom, nextTo, view?.runs.length ?? 0);
    setFromIndex(pair.from);
    setToIndex(pair.to);
    void load(pair.from, pair.to);
  };

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">运行对比</h1>
        <p className="page-sub">
          任意两次已完成运行之间：哪些 finding 新增、修复、回归或未变
        </p>
      </div>
      <button className="btn" onClick={() => void load()} disabled={loading}>
        {loading ? "刷新中…" : "刷新"}
      </button>
    </div>
  );

  if (loading && !view) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 读取运行历史…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">加载运行对比失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!view) return <>{header}</>;

  if (view.runs.length < 2) {
    return (
      <>
        {header}
        <section className="panel">
          <h2>至少需要两次已完成的运行</h2>
          <p className="muted">
            对比按 finding 指纹把两次运行放在一起看；当前只有 {view.runs.length} 次已完成运行
            （<code>.iterate/decision-log.jsonl</code> 里的 <code>report</code> 条目）。
          </p>
          <pre className="cmd-hint">ih iterate run</pre>
          <p className="muted">再完成一次运行后回来刷新即可对比。</p>
        </section>
      </>
    );
  }

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次数据）：{error}</p>}

      <div className="filter-row">
        <label>
          较早一次
          <select
            value={fromIndex}
            disabled={loading}
            onChange={(event) => choose(Number(event.target.value), toIndex)}
          >
            {view.runs.map((run) => (
              <option key={run.index} value={run.index}>
                {runOptionLabel(run)}
              </option>
            ))}
          </select>
        </label>
        <label>
          较晚一次
          <select
            value={toIndex}
            disabled={loading}
            onChange={(event) => choose(fromIndex, Number(event.target.value))}
          >
            {view.runs.map((run) => (
              <option key={run.index} value={run.index}>
                {runOptionLabel(run)}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{diffSummary(view.counts)}</span>
      </div>

      <div className="cards">
        <RunCard label="较早一次" run={view.from_run} />
        <RunCard label="较晚一次" run={view.to_run} />
        <div className="card">
          <div className="k">净变化</div>
          <div className="v small">
            {view.counts.fixed - view.counts.new - view.counts.regressed > 0
              ? `减少 ${view.counts.fixed - view.counts.new - view.counts.regressed}`
              : view.counts.fixed - view.counts.new - view.counts.regressed < 0
                ? `增加 ${Math.abs(view.counts.fixed - view.counts.new - view.counts.regressed)}`
                : "持平"}
          </div>
          <div className="muted card-note">修复 − 新增 − 回归</div>
        </div>
      </div>

      {DIFF_SECTIONS.map((spec) => {
        const findings = view[spec.key];
        return (
          <section className="panel" key={spec.key}>
            <h2>
              {spec.label} <span className={`badge ${spec.tone}`}>{findings.length}</span>
            </h2>
            <p className="muted" style={{ marginTop: 0 }}>
              {spec.hint}
            </p>
            {findings.length === 0 ? (
              <p className="empty">无</p>
            ) : (
              <FindingTable findings={findings} />
            )}
          </section>
        );
      })}

      <p className="muted">
        同一视图也可从终端得到：<code>ih iterate diff --from {view.from_run?.index} --to {view.to_run?.index}</code>
        ，加 <code>--json</code> 供脚本消费。回归判定使用趋势库里已修复的指纹，因此“以前修过、这次又出现”不会被算成新增。
      </p>
    </>
  );
}
