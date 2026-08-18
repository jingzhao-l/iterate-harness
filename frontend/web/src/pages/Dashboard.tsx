// Dashboard (design §17.3 P1) — runtime status cards, convergence curve,
// latest run summary, report entries and recent audit log. Data comes from
// the shared store (kept fresh by the SSE stream + periodic refresh).

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ConvergenceChart } from "../components/ConvergenceChart";
import { StartDialog } from "../components/StartDialog";
import { useWebUi } from "../store";

const SEVERITY_LABELS: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return "$0.0000";
  return `$${value.toFixed(4)}`;
}

export default function Dashboard(): React.JSX.Element {
  const status = useWebUi((state) => state.status);
  const statusLoading = useWebUi((state) => state.statusLoading);
  const lastError = useWebUi((state) => state.lastError);
  const refreshStatus = useWebUi((state) => state.refreshStatus);
  const chatStatus = useWebUi((state) => state.chatStatus);
  const pushToast = useWebUi((state) => state.pushToast);
  const [showStart, setShowStart] = useState(false);

  useEffect(() => {
    if (!status && !lastError) void refreshStatus();
  }, [status, lastError, refreshStatus]);

  if (statusLoading && !status) {
    return (
      <div className="loading-block">
        <span className="spinner" /> 加载运行状态…
      </div>
    );
  }

  if (lastError && !status) {
    return (
      <>
        <h1 className="page-title">仪表盘</h1>
        <p className="page-sub">iterate-harness 本地管理台</p>
        <section className="panel">
          <p className="muted">无法连接后端服务：{lastError}</p>
          <button className="btn primary" onClick={() => void refreshStatus()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!status) return <div className="empty">暂无数据</div>;

  const { budget, config } = status;
  const lastRun = status.last_run;
  const runActive = chatStatus?.state === "running" || chatStatus?.state === "starting" || chatStatus?.state === "paused";

  // Persistent run-status banner, decoupled from the chat panel: it reads the
  // real status source (store.status converged + store.chatStatus run state fed
  // by the SSE run-state/progress-update events and the REST poll fallback).
  const liveConverged = status.converged === true || chatStatus?.converged === true;
  const chatState = chatStatus?.state;
  const waitingForInput =
    chatStatus?.waiting_for === "permission" ||
    chatStatus?.waiting_for === "user_select" ||
    chatStatus?.waiting_for === "user_prompt";
  let bannerLabel = "空闲 · 等待启动";
  let bannerKind = "idle";
  if (chatStatus?.error) {
    bannerLabel = "运行失败";
    bannerKind = "failed";
  } else if (liveConverged) {
    bannerLabel = "已收敛";
    bannerKind = "converged";
  } else if (chatState === "running" || chatState === "starting") {
    bannerLabel = chatState === "starting" ? "启动中" : "运行中";
    bannerKind = chatState === "starting" ? "starting" : "running";
  } else if (chatState === "paused") {
    bannerLabel = "暂停 · 等待输入";
    bannerKind = "paused";
  } else if (chatState === "stopped") {
    bannerLabel = "已停止";
    bannerKind = "stopped";
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">仪表盘</h1>
          <p className="page-sub mono">{status.project_root}</p>
        </div>
        <button
          className="btn primary start-run-btn"
          onClick={() => setShowStart(true)}
          disabled={runActive}
          title={runActive ? "已有运行中的 iterate 循环" : "启动一次 iterate 循环"}
        >
          {runActive ? "运行中…" : "启动迭代"}
        </button>
      </div>

      {/* Persistent run status (decoupled from the chat panel). */}
      <section className={`run-banner ${bannerKind}`}>
        <span className={`state-badge state-${bannerKind}`}>{bannerLabel}</span>
        {waitingForInput && <span className="waiting-tag">需要你的输入</span>}
        {chatStatus?.run_id && (
          <span className="muted mono run-banner-id">
            运行 #{chatStatus.run_id}
            {chatStatus.project_root ? ` · ${chatStatus.project_root}` : ""}
          </span>
        )}
      </section>

      <div className="cards">
        <div className="card">
          <div className="k">运行模式</div>
          <div className="v small">{lastRun?.mode ?? config.mode ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">最新判定</div>
          <div className="v small">
            {lastRun?.verdict ?? "—"}
          </div>
        </div>
        <div className="card">
          <div className="k">决策条目</div>
          <div className="v">{status.entry_count}</div>
        </div>
        <div className="card">
          <div className="k">已完成轮次</div>
          <div className="v">{status.latest_round}</div>
        </div>
        <div className="card">
          <div className="k">累计 Tokens</div>
          <div className="v small">{formatTokens(budget.usedTokens)}</div>
        </div>
        <div className="card">
          <div className="k">累计成本</div>
          <div className="v small">{formatUsd(budget.usedUsd)}</div>
        </div>
      </div>

      <section className="panel">
        <h2>收敛曲线（每轮 findings 数）</h2>
        <ConvergenceChart values={status.convergence} />
      </section>

      {lastRun && (
        <section className="panel">
          <h2>最近一次运行</h2>
          <p className="muted mono" style={{ margin: "0 0 10px" }}>
            {lastRun.timestamp ?? "—"} · 模式 {lastRun.mode ?? "—"} · 轮次{" "}
            {lastRun.rounds ?? "—"}
            {lastRun.interrupted ? " · 中断" : ""}
          </p>
          {lastRun.totalFindings !== undefined && (
            <p>
              共 {lastRun.totalFindings} 个 findings：
              {Object.entries(lastRun.severity ?? {}).map(([severity, count]) => (
                <span key={severity} style={{ marginLeft: 12 }}>
                  <span className={`badge severity-${severity}`}>
                    {SEVERITY_LABELS[severity] ?? severity}
                  </span>{" "}
                  {String(count)}
                </span>
              ))}
            </p>
          )}
          {lastRun.preview && lastRun.preview.length > 0 && (
            <table className="data">
              <thead>
                <tr>
                  <th>严重度</th>
                  <th>文件</th>
                  <th>维度</th>
                  <th>摘要</th>
                </tr>
              </thead>
              <tbody>
                {lastRun.preview.map((finding, index) => (
                  <tr key={index}>
                    <td>
                      <span className={`badge severity-${finding.severity}`}>
                        {SEVERITY_LABELS[finding.severity] ?? finding.severity}
                      </span>
                    </td>
                    <td className="mono">{finding.file}</td>
                    <td>{finding.dimension}</td>
                    <td>{finding.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <section className="panel" style={{ margin: "16px 0 0" }}>
          <h2>最近报告</h2>
          {status.reports.length === 0 ? (
            <p className="empty">暂无报告产物</p>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>大小</th>
                </tr>
              </thead>
              <tbody>
                {status.reports.map((report) => (
                  <tr key={report.name}>
                    <td>
                      <Link to={`/reports?name=${encodeURIComponent(report.name)}`}>
                        {report.name}
                      </Link>
                    </td>
                    <td className="mono">{report.size} B</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="panel" style={{ margin: "16px 0 0" }}>
          <h2>最近审计记录</h2>
          {status.audit_recent.length === 0 ? (
            <p className="empty">暂无写操作记录</p>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作</th>
                  <th>目标</th>
                </tr>
              </thead>
              <tbody>
                {status.audit_recent.map((entry, index) => (
                  <tr key={index}>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>
                      {entry.timestamp.slice(5, 19)}
                    </td>
                    <td className="mono">{entry.action}</td>
                    <td>{entry.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {showStart && (
        <StartDialog
          onClose={() => setShowStart(false)}
          onStarted={() => {
            setShowStart(false);
            pushToast("success", "迭代已启动，可在右下角对话面板查看进度");
          }}
        />
      )}
    </>
  );
}
