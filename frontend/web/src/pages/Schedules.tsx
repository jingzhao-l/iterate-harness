// Scheduled reviews (design §17.3 P10) — the console's view of the machine's
// cron registry and daemon. `ih iterate schedule` / `ih cron` could answer
// "what is scheduled, and did last night's run pass?" only from a terminal;
// this page answers both, including the case where jobs exist but the daemon
// is stopped (nothing will fire). Read-only: jobs are added/removed from the CLI.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import {
  durationLabel,
  historyNewestFirst,
  historyTone,
  jobNeedsAttention,
  nextRunLabel,
  schedulerHeadline,
} from "../schedules";
import type { CronHistoryView, CronJobView, SchedulesView } from "../types";

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function JobRow({ job }: { job: CronJobView }): React.JSX.Element {
  return (
    <tr className={jobNeedsAttention(job) ? "row-warn" : ""}>
      <td className="mono">{job.name || "—"}</td>
      <td className="mono">{job.schedule || "—"}</td>
      <td>{job.timezone || "UTC"}</td>
      <td>
        {job.schedule_valid ? nextRunLabel(job.next_run) : <span className="badge severity-critical">无效表达式</span>}
      </td>
      <td className="mono">{job.last_run ? shortTime(job.last_run) : "从未运行"}</td>
      <td>
        {job.last_status ? (
          <span className={`badge ${historyTone(job.last_status)}`}>{job.last_status}</span>
        ) : (
          "—"
        )}
        {!job.enabled && <span className="badge gray"> 已停用</span>}
      </td>
      <td className="mono" title={job.command}>
        {job.command || "—"}
        <div className="muted mono small">{job.cwd || "—"}</div>
      </td>
    </tr>
  );
}

function HistoryRow({ entry }: { entry: CronHistoryView }): React.JSX.Element {
  return (
    <tr>
      <td className="mono">{shortTime(entry.started_at)}</td>
      <td className="mono">{entry.name || "—"}</td>
      <td>
        <span className={`badge ${historyTone(entry.status)}`}>{entry.status || "?"}</span>
      </td>
      <td className="mono">{entry.returncode ?? "—"}</td>
      <td className="mono">{durationLabel(entry.duration_s)}</td>
      <td>
        {entry.stdout_tail || entry.stderr_tail ? (
          <details>
            <summary className="muted">查看输出</summary>
            {entry.stderr_tail && (
              <>
                <div className="muted small">stderr</div>
                <pre className="cmd-hint">{entry.stderr_tail}</pre>
              </>
            )}
            <div className="muted small">stdout</div>
            <pre className="cmd-hint">{entry.stdout_tail || "（无输出）"}</pre>
          </details>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

export default function Schedules(): React.JSX.Element {
  const [view, setView] = useState<SchedulesView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.schedules(100));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const history = useMemo(() => historyNewestFirst(view?.history ?? []), [view]);
  const scheduler = view?.scheduler ?? null;
  const headline = schedulerHeadline(scheduler);
  const jobs = view?.jobs ?? [];
  const latestRun = history[0] ?? null;

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">定时任务</h1>
        <p className="page-sub">本机调度器：计划、下次运行与最近执行结果</p>
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
          <span className="spinner" /> 读取定时任务…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取定时任务失败：{error}</p>
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

      <p className="muted" style={{ marginTop: 0 }}>
        定时任务属于本机（<code>~/.iterate-harness/data</code>），不随项目根目录切换；这里只读展示，
        增删计划请用 <code>ih iterate schedule</code>。
      </p>

      <div className="cards">
        <div className="card">
          <div className="k">调度器</div>
          <div className={`v small direction-${headline.tone}`}>{headline.label}</div>
          <div className="muted card-note">{headline.detail}</div>
        </div>
        <div className="card">
          <div className="k">计划任务</div>
          <div className="v">{scheduler?.enabled_jobs ?? 0}</div>
          <div className="muted card-note">启用 / 共 {scheduler?.total_jobs ?? 0} 个</div>
        </div>
        <div className="card">
          <div className="k">最近一次执行</div>
          <div className="v small">
            {latestRun ? (
              <span className={`badge ${historyTone(latestRun.status)}`}>{latestRun.status || "?"}</span>
            ) : (
              "—"
            )}
          </div>
          <div className="muted card-note">
            {latestRun ? `${shortTime(latestRun.started_at)} · ${latestRun.name || "?"}` : "还没有执行记录"}
          </div>
        </div>
        <div className="card">
          <div className="k">日志 / 历史</div>
          <div className="v small mono" title={scheduler?.log_file ?? undefined}>
            {scheduler?.log_file ? scheduler.log_file.split("/").pop() : "—"}
          </div>
          <div className="muted card-note mono" title={scheduler?.history_file ?? undefined}>
            {scheduler?.history_file ? scheduler.history_file.split("/").pop() : "—"}
          </div>
        </div>
      </div>

      {jobs.length > 0 && scheduler && !scheduler.running && (
        <section className="panel">
          <h2>调度器未运行</h2>
          <p className="muted">
            计划已登记，但本机调度器没有启动，到点也不会执行。启动后再回来刷新：
          </p>
          <pre className="cmd-hint">ih iterate cron start</pre>
        </section>
      )}

      <section className="panel">
        <h2>计划</h2>
        {jobs.length === 0 ? (
          <>
            <p className="muted">
              还没有登记任何计划。下面这条命令登记每日（UTC）09:00 的 changed-only 快审：
            </p>
            <pre className="cmd-hint">ih iterate schedule add --cron "0 9 * * 1-5"</pre>
            <p className="muted">
              登记后还需要让调度器常驻：<code>ih iterate cron start</code>；随时用{" "}
              <code>ih iterate cron status</code> 查看它是否在跑。
            </p>
          </>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>计划</th>
                  <th>时区</th>
                  <th>下次运行</th>
                  <th>上次运行</th>
                  <th>上次结果</th>
                  <th>命令 / 目录</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <JobRow key={job.name} job={job} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>执行历史</h2>
        {history.length === 0 ? (
          <p className="empty">
            还没有执行记录。调度器每次跑完一个计划都会在 <code>cron_history.jsonl</code> 里留一行。
          </p>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              最新在前，最多显示最近 {history.length} 次；输出只保留尾部，完整日志见上面的日志文件。
            </p>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>开始</th>
                    <th>任务</th>
                    <th>状态</th>
                    <th>退出码</th>
                    <th>耗时</th>
                    <th>输出</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((entry, index) => (
                    <HistoryRow key={`${entry.started_at}-${entry.name}-${index}`} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
