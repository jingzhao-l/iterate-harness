// RunStatusCard — live iterate run state + control buttons for the chat page
// (design §18.4). The harness center is the run, not the conversation, so this
// card sits above the message stream and always shows the current state.

import type { ChatRunStatus, ChatRunState } from "../types";

const STATE_LABELS: Record<ChatRunState, string> = {
  idle: "空闲",
  starting: "启动中",
  running: "运行中",
  paused: "等待输入",
  stopped: "已停止",
};

const STATE_CLASS: Record<ChatRunState, string> = {
  idle: "state-idle",
  starting: "state-starting",
  running: "state-running",
  paused: "state-paused",
  stopped: "state-stopped",
};

const MODE_LABELS: Record<string, string> = {
  review: "评审（dry-run）",
  run: "修复（normal）",
  resume: "续跑（resume）",
};

function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return "$0.0000";
  return `$${value.toFixed(4)}`;
}

interface RunStatusCardProps {
  status: ChatRunStatus | null;
  loading: boolean;
  busy: boolean;
  onControl: (action: "pause" | "resume" | "stop") => void;
  onStart: () => void;
}

export default function RunStatusCard({
  status,
  loading,
  busy,
  onControl,
  onStart,
}: RunStatusCardProps): React.JSX.Element {
  if (loading && !status) {
    return (
      <section className="panel run-status">
        <span className="spinner" /> 加载运行状态…
      </section>
    );
  }

  if (!status) {
    return (
      <section className="panel run-status">
        <p className="muted" style={{ margin: 0 }}>
          尚无运行记录。可在仪表盘点击「启动迭代」，或在此直接启动。
        </p>
        <button className="btn primary" onClick={onStart} disabled={busy}>
          启动迭代
        </button>
      </section>
    );
  }

  const waiting =
    status.waiting_for === "permission" ||
    status.waiting_for === "user_select" ||
    status.waiting_for === "user_prompt";

  return (
    <section className={`panel run-status ${STATE_CLASS[status.state]}`}>
      <div className="run-status-head">
        <span className={`state-badge ${STATE_CLASS[status.state]}`}>
          {STATE_LABELS[status.state]}
        </span>
        {waiting && <span className="waiting-tag">需要你的输入</span>}
        {status.converged && <span className="waiting-tag ok">已收敛</span>}
        {status.error && <span className="waiting-tag err">错误</span>}
      </div>

      <div className="run-metrics">
        <div className="metric">
          <span className="k">模式</span>
          <span className="v">{MODE_LABELS[status.mode] ?? status.mode ?? "—"}</span>
        </div>
        <div className="metric">
          <span className="k">轮次</span>
          <span className="v">{status.round}</span>
        </div>
        <div className="metric">
          <span className="k">本轮新 findings</span>
          <span className="v">+{status.new_findings}</span>
        </div>
        <div className="metric">
          <span className="k">累计 findings</span>
          <span className="v">{status.total_findings}</span>
        </div>
        <div className="metric">
          <span className="k">成本</span>
          <span className="v small">{formatUsd(status.cost_usd)}</span>
        </div>
      </div>

      {status.run_id && (
        <p className="muted mono run-id">
          运行 #{status.run_id} · {status.project_root}
        </p>
      )}
      {status.message && <p className="muted run-note">{status.message}</p>}

      <div className="run-controls">
        {status.state === "running" && !waiting && (
          <button className="btn" onClick={() => onControl("pause")} disabled={busy}>
            暂停
          </button>
        )}
        {status.waiting_for === "user_select" && (
          <button className="btn" onClick={() => onControl("resume")} disabled={busy}>
            继续
          </button>
        )}
        {(status.state === "running" || status.state === "paused") && (
          <button className="btn danger" onClick={() => onControl("stop")} disabled={busy}>
            停止
          </button>
        )}
      </div>
    </section>
  );
}
