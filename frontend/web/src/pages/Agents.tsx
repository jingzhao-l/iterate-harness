// Team roster (design §17.3 P9) — who the harness is actually running right
// now, for how long, and what each worker last said. This is the only place
// that could answer "which worker is stuck": every backend writes one store,
// and this page is its projection.
//
// Read-only by default and safe to leave open; the one mutation is 停止,
// which goes through a secondary-confirmation dialog and is audited
// server-side.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import {
  STATUS_FILTERS,
  filterAgents,
  formatBytes,
  formatDuration,
  kindLabel,
  lastLine,
  primaryId,
  rosterKpis,
  statusLabel,
  type StatusFilter,
} from "../agents";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useWebUi } from "../store";
import type { AgentRow, AgentsView } from "../types";

const DEFAULT_TAIL = 2000;
const DEFAULT_LIMIT = 100;
const AUTO_REFRESH_MS = 5000;

function shortTime(timestamp: string | null): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function StatusBadge({ status, running }: { status: string; running: boolean }): React.JSX.Element {
  const tone =
    status === "failed"
      ? "severity-critical"
      : status === "killed"
        ? "gray"
        : status === "completed"
          ? "green"
          : running
            ? "amber"
            : "neutral";
  return <span className={`badge ${tone}`}>{statusLabel(status)}</span>;
}

function KindBadge({ kind, backend }: { kind: string; backend: string }): React.JSX.Element {
  const tone =
    kind === "in_process" ? "green" : kind === "subprocess" ? "amber" : "neutral";
  return (
    <span className={`badge ${tone}`} title={`后端：${backend}`}>
      {kindLabel(kind)}
    </span>
  );
}

interface AgentRowProps {
  agent: AgentRow;
  expanded: boolean;
  onToggle: () => void;
  onStop: () => void;
}

function AgentRowItem({ agent, expanded, onToggle, onStop }: AgentRowProps): React.JSX.Element {
  return (
    <tr className={agent.running ? "row-live" : "row-dim"}>
      <td>
        <button
          className="row-toggle"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`展开 ${primaryId(agent)}`}
        >
          {expanded ? "▾" : "▸"}
        </button>
      </td>
      <td>
        <span className="mono strong" title={agent.id}>
          {primaryId(agent)}
        </span>
        {agent.team && <span className="muted"> @{agent.team}</span>}
      </td>
      <td>
        <KindBadge kind={agent.kind} backend={agent.backend} />
      </td>
      <td>
        <StatusBadge status={agent.status} running={agent.running} />
        {agent.running && <span className="muted live-dot" aria-hidden="true" />}
      </td>
      <td className="mono">{formatDuration(agent.duration_s)}</td>
      <td className="mono">
        {agent.status === "failed" || agent.status === "killed"
          ? `退出码 ${agent.return_code ?? "?"}`
          : formatBytes(agent.output_bytes)}
      </td>
      <td className="agent-desc">
        <div className="row-main">
          {agent.status_note || lastLine(agent.tail) || agent.description || "—"}
        </div>
      </td>
      <td>
        {agent.running ? (
          <button
            className="btn danger"
            onClick={onStop}
            title="向该子代理发送停止请求"
          >
            停止
          </button>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      {expanded && (
        <td className="agent-detail" colSpan={7}>
          <dl className="detail-grid">
            <dt>任务 ID</dt>
            <dd className="mono">{agent.id}</dd>
            <dt>子代理</dt>
            <dd className="mono">{agent.agent_id || "（无，Shell 任务）"}</dd>
            <dt>团队</dt>
            <dd>{agent.team || "—"}</dd>
            <dt>任务类型</dt>
            <dd className="mono">{agent.task_type || "—"}</dd>
            <dt>开始</dt>
            <dd className="mono">{shortTime(agent.started_at)}</dd>
            <dt>结束</dt>
            <dd className="mono">{shortTime(agent.ended_at)}</dd>
            <dt>进度</dt>
            <dd>{agent.progress || "—"}</dd>
            <dt>工作目录</dt>
            <dd className="mono">{agent.cwd || "—"}</dd>
            <dt>输出大小</dt>
            <dd className="mono">{formatBytes(agent.output_bytes)}</dd>
          </dl>
          <h4>输出尾部</h4>
          {agent.tail ? (
            <pre className="code-block">{agent.tail}</pre>
          ) : (
            <p className="empty small">该子代理还没有输出。</p>
          )}
        </td>
      )}
    </tr>
  );
}

export default function Agents(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);

  const [view, setView] = useState<AgentsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [withTail, setWithTail] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [toStop, setToStop] = useState<AgentRow | null>(null);
  const [stopping, setStopping] = useState(false);
  const [stopMessage, setStopMessage] = useState<string | null>(null);

  const load = useCallback(async (silent = false): Promise<void> => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const next = await api.agents(projectRoot, {
        status: statusFilter === "all" ? undefined : statusFilter,
        tail: withTail ? DEFAULT_TAIL : 0,
        limit: DEFAULT_LIMIT,
      });
      setView(next);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [projectRoot, statusFilter, withTail]);

  useEffect(() => {
    void load();
  }, [load]);

  // A live team changes constantly, so poll while any worker is still running.
  // Nothing is polled once the team is empty or all-terminal: there is
  // nothing new to show, and an idle console should not keep a socket busy.
  const hasLive = (view?.agents ?? []).some((agent) => agent.running);
  useEffect(() => {
    if (!autoRefresh || !hasLive || loading) return;
    const timer = window.setInterval(() => {
      void load(true);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, hasLive, loading, load]);

  const agents = view?.agents ?? [];
  const visible = useMemo(() => filterAgents(agents, { status: "all", query }), [agents, query]);
  const kpis = useMemo(() => rosterKpis(agents), [agents]);

  const toggle = (id: string): void => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const doStop = async (): Promise<void> => {
    if (!toStop) return;
    setStopping(true);
    setStopMessage(null);
    try {
      const result = await api.shutdownAgent(primaryId(toStop), projectRoot);
      setToStop(null);
      setStopMessage(
        result.status === "stopped"
          ? `已停止 ${result.id ?? result.agent_id}`
          : result.status === "not_found"
            ? "没有找到该子代理（它可能已经退出）"
            : result.status === "already_terminal"
              ? `该子代理已处于 ${result.status_now ?? "终态"}`
              : `停止失败：${result.message}`,
      );
      void load(true);
    } catch (caught) {
      setStopMessage(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setStopping(false);
    }
  };

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">团队</h1>
        <p className="page-sub">
          当前在跑的子代理、Shell 任务与它们的输出尾部
          {view && <span className="muted"> · 快照 {shortTime(view.generated_at)}</span>}
        </p>
      </div>
      <div className="actions">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          有在跑的子代理时每 5 秒刷新
        </label>
        <button
          className="btn"
          onClick={() => void load(true)}
          disabled={refreshing || loading}
        >
          {refreshing || loading ? "刷新中…" : "刷新"}
        </button>
      </div>
    </div>
  );

  if (loading && !view) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 正在读取团队状态…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取团队失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次数据）：{error}</p>}
      {stopMessage && <p className="empty">{stopMessage}</p>}

      <div className="cards">
        <div className="card">
          <div className="k">在跑</div>
          <div className="v">{kpis.running}</div>
        </div>
        <div className="card">
          <div className="k">失败</div>
          <div className="v">{kpis.failed}</div>
        </div>
        <div className="card">
          <div className="k">已停止</div>
          <div className="v">{kpis.killed}</div>
        </div>
        <div className="card">
          <div className="k">完成</div>
          <div className="v">{kpis.completed}</div>
        </div>
        <div className="card">
          <div className="k">在册</div>
          <div className="v">{kpis.total}</div>
          <div className="muted card-note">
            {view?.truncated
              ? `还有 ${view.total - view.agents.length} 条未加载`
              : "全部已加载"}
          </div>
        </div>
      </div>

      {agents.length === 0 ? (
        <section className="panel">
          <h2>没有在册的子代理</h2>
          <p className="muted">
            子进程子代理、进程内子代理和 Shell 任务都会出现在这里。启动一次循环
            <code> ih iterate run --rounds 3</code>，或在引擎里派出一个 teammate 之后回来查看。
          </p>
        </section>
      ) : (
        <section className="panel">
          <h2>子代理</h2>
          <div className="chip-row">
            {STATUS_FILTERS.map((option) => (
              <button
                key={option.value}
                className={`chip ${statusFilter === option.value ? "active" : ""}`}
                onClick={() => setStatusFilter(option.value)}
                aria-pressed={statusFilter === option.value}
              >
                {option.label}
              </button>
            ))}
            <input
              type="search"
              className="search-input"
              placeholder="搜索名称、团队、任务、输出…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="搜索子代理"
            />
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={withTail}
                onChange={(event) => setWithTail(event.target.checked)}
              />
              加载输出尾部
            </label>
          </div>

          {refreshing && <div className="refresh-bar" aria-hidden="true" />}

          {visible.length === 0 ? (
            <p className="empty">没有匹配 {query || "当前筛选条件"} 的子代理。</p>
          ) : (
            <div className="table-wrap">
              <table className="data agents-table">
                <thead>
                  <tr>
                    <th aria-label="展开" />
                    <th>子代理 / 任务</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>已运行</th>
                    <th>输出</th>
                    <th>最近输出</th>
                    <th aria-label="操作" />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((agent) => (
                    <AgentRowItem
                      key={agent.id}
                      agent={agent}
                      expanded={expanded.has(agent.id)}
                      onToggle={() => toggle(agent.id)}
                      onStop={() => setToStop(agent)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {view?.truncated && (
            <p className="muted">
              只加载了最近的 {view.agents.length} 条（共 {view.total} 条），最早的记录未显示。
            </p>
          )}
        </section>
      )}

      {toStop && (
        <ConfirmDialog
          title="停止子代理"
          confirmLabel="停止"
          danger
          busy={stopping}
          onConfirm={doStop}
          onCancel={() => {
            setToStop(null);
            setStopMessage(null);
          }}
        >
          将向 <span className="mono strong">{primaryId(toStop)}</span> 发送停止请求。子进程会收到
          进程组信号，进程内子代理会被协作式取消；它正在做的当前步骤不会被保存。任务 ID：
          <span className="mono">{toStop.id}</span>
        </ConfirmDialog>
      )}
    </>
  );
}
