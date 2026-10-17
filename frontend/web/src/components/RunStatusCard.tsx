// RunStatusCard — live iterate run state + control buttons for the chat page
// (design §18.4). The harness center is the run, not the conversation, so this
// card sits above the message stream and always shows the current state.

import type { ChatRunStatus, ChatRunState, PermissionMode } from "../types";

// Human labels for the permission posture. `full_auto` writes without asking,
// so it must be visible on the run card — the field existed on the wire but
// was never rendered, leaving a full-auto run indistinguishable from a
// plan/default one.
const PERMISSION_MODE_LABELS: Record<PermissionMode, string> = {
  full_auto: "全自动（不再确认）",
  plan: "计划（只读）",
  default: "默认（改动前确认）",
};

// `pausing` / `stopping` are the *pending-transition* states: a pause only
// takes effect at the next round boundary and a stop is still cancelling, so
// the badge has to say "requested" — otherwise the operator clicks 暂停 and the
// card still reads 运行中 with no sign the click registered.
const STATE_LABELS: Record<ChatRunState, string> = {
  idle: "空闲",
  starting: "启动中",
  running: "运行中",
  paused: "等待输入",
  pausing: "暂停中…",
  stopping: "停止中…",
  stopped: "已停止",
};

const STATE_CLASS: Record<ChatRunState, string> = {
  idle: "state-idle",
  starting: "state-starting",
  running: "state-running",
  paused: "state-paused",
  pausing: "state-paused",
  stopping: "state-stopped",
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
  // A pending transition pulses so the badge reads as "in flight", not settled.
  const pending = status.state === "pausing" || status.state === "stopping";

  return (
    <section className={`panel run-status ${STATE_CLASS[status.state]}`}>
      <div className="run-status-head">
        <span
          className={`state-badge ${STATE_CLASS[status.state]}${pending ? " state-pending" : ""}`}
        >
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
        {status.permission_mode && (
          <div className="metric">
            <span className="k">权限姿态</span>
            <span
              className={`v small${status.permission_mode === "full_auto" ? " warn" : ""}`}
              title={
                status.permission_mode === "full_auto"
                  ? "本次运行会自动执行工具，不逐次请求确认"
                  : "本次运行在需要时会请求确认"
              }
            >
              {PERMISSION_MODE_LABELS[status.permission_mode] ?? status.permission_mode}
            </span>
          </div>
        )}
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
        {(status.state === "running" ||
          status.state === "starting" ||
          status.state === "paused" ||
          status.state === "pausing") && (
          <button className="btn danger" onClick={() => onControl("stop")} disabled={busy}>
            停止
          </button>
        )}
      </div>
    </section>
  );
}
