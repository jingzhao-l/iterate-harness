// Scheduled reviews (design §17.3 P10) — the console's view of the machine's
// cron registry and daemon, and the place to register/pause/remove a plan.
// `ih iterate schedule` / `ih cron` could answer "what is scheduled, and did
// last night's run pass?" only from a terminal; this page answers both and
// closes the loop: saving a plan, pausing one and deleting one are all here,
// each behind the same secondary confirmation the rest of the console uses.
//
// The registry is machine-global, so plans do not follow the sidebar's project
// root automatically — the form carries an explicit project directory (it
// defaults to the current root) and moving an existing plan to another
// directory asks before it happens.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  buildScheduleInput,
  canonicalJob,
  defaultScheduleForm,
  describeScheduleMode,
  movesExistingSchedule,
  type ScheduleFormState,
} from "../scheduleForm";
import {
  durationLabel,
  historyNewestFirst,
  historyTone,
  jobNeedsAttention,
  nextRunLabel,
  schedulerHeadline,
} from "../schedules";
import { useWebUi } from "../store";
import type {
  CronHistoryView,
  CronJobView,
  ScheduleUpsertInput,
  SchedulesView,
} from "../types";

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function JobRow({
  job,
  busy,
  onToggle,
  onRemove,
}: {
  job: CronJobView;
  busy: boolean;
  onToggle: (job: CronJobView) => void;
  onRemove: (job: CronJobView) => void;
}): React.JSX.Element {
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
      <td>
        <div className="actions">
          <button
            className="btn small"
            disabled={busy}
            onClick={() => onToggle(job)}
            title={job.enabled ? "停止到点执行（不删除计划）" : "恢复在调度器中的到点执行"}
          >
            {busy ? "处理中…" : job.enabled ? "停用" : "启用"}
          </button>
          <button className="btn small danger" disabled={busy} onClick={() => onRemove(job)}>
            删除
          </button>
        </div>
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
  const projectRoot = useWebUi((state) => state.projectRoot);
  const pushToast = useWebUi((state) => state.pushToast);

  const [view, setView] = useState<SchedulesView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<ScheduleFormState>(() => defaultScheduleForm(projectRoot));
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [pendingReplace, setPendingReplace] = useState<ScheduleUpsertInput | null>(null);
  const [pendingRemove, setPendingRemove] = useState<CronJobView | null>(null);
  const lastRoot = useRef(projectRoot);

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

  // Follow the sidebar's project root into the form only while the operator
  // has not typed a different directory themselves.
  useEffect(() => {
    const previous = lastRoot.current;
    lastRoot.current = projectRoot;
    setForm((current) =>
      current.projectRoot === previous ? { ...current, projectRoot } : current,
    );
  }, [projectRoot]);

  const history = view?.history ?? [];
  const orderedHistory = historyNewestFirst(history);
  const scheduler = view?.scheduler ?? null;
  const headline = schedulerHeadline(scheduler);
  const jobs = view?.jobs ?? [];
  const latestRun = orderedHistory[0] ?? null;
  const existing = canonicalJob(view);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setView(await api.schedules(100));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  const save = useCallback(
    async (input: ScheduleUpsertInput, replace = false): Promise<void> => {
      setSaving(true);
      setFormErrors([]);
      try {
        const result = await api.upsertSchedule({ ...input, replace });
        pushToast("success", result.message);
        setPendingReplace(null);
        await refresh();
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 409) {
          // The server says the canonical job lives somewhere else. Keep the
          // operator's input and ask before moving it.
          setPendingReplace(input);
          setFormErrors([caught.message]);
        } else {
          setFormErrors([caught instanceof Error ? caught.message : String(caught)]);
        }
      } finally {
        setSaving(false);
      }
    },
    [pushToast, refresh],
  );

  const onSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const result = buildScheduleInput(form);
    if (!result.input) {
      setFormErrors(result.errors);
      return;
    }
    if (movesExistingSchedule(existing, form.projectRoot.trim())) {
      setFormErrors([]);
      setPendingReplace(result.input);
      return;
    }
    void save(result.input);
  };

  const toggleJob = async (job: CronJobView): Promise<void> => {
    setBusyJob(job.name);
    try {
      const result = await api.setScheduleEnabled(job.name, !job.enabled, projectRoot);
      pushToast("success", result.message);
      await refresh();
    } catch (caught) {
      pushToast("error", caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyJob(null);
    }
  };

  const removeJob = async (job: CronJobView): Promise<void> => {
    setBusyJob(job.name);
    try {
      const result = await api.removeSchedule(job.name, projectRoot);
      pushToast("success", result.message);
      setPendingRemove(null);
      await refresh();
    } catch (caught) {
      pushToast("error", caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyJob(null);
    }
  };

  const setField = <K extends keyof ScheduleFormState>(key: K, value: ScheduleFormState[K]): void => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">定时任务</h1>
        <p className="page-sub">本机调度器：登记、暂停、删除计划，并查看最近执行结果</p>
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
        定时任务属于本机（<code>~/.iterate-harness/data</code>），不随项目根目录自动切换；下方表单显式记录要为哪个项目登记。
        命令行等价入口：<code>ih iterate schedule</code> / <code>ih iterate cron</code>。
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
        <h2>{existing ? "更新 quick-review 计划" : "登记 quick-review 计划"}</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          {existing
            ? `本机已有 ${existing.name}，保存会更新它的时间与参数。`
            : "登记后还需要调度器常驻；本机没有计划时，这条命令可随时补登记。"}
          {existing && movesExistingSchedule(existing, form.projectRoot.trim()) && (
            <> 当前计划指向 <code>{existing.cwd || "(未知目录)"}</code>，保存到另一个目录前会再确认一次。</>
          )}
        </p>
        <form onSubmit={onSubmit}>
          <div className="schedule-form">
            <label className="field-label">
              项目目录
              <input
                type="text"
                value={form.projectRoot}
                placeholder="留空 = 当前项目根目录"
                onChange={(event) => setField("projectRoot", event.target.value)}
              />
            </label>
            <label className="field-label">
              cron 表达式
              <input
                type="text"
                value={form.schedule}
                placeholder="0 9 * * 1-5"
                onChange={(event) => setField("schedule", event.target.value)}
              />
            </label>
            <label className="field-label">
              时区（可选）
              <input
                type="text"
                value={form.timezone}
                placeholder="Asia/Shanghai（留空 = UTC）"
                onChange={(event) => setField("timezone", event.target.value)}
              />
            </label>
            <label className="field-label">
              模式
              <select
                value={form.mode}
                onChange={(event) => setField("mode", event.target.value as "dry-run" | "normal")}
              >
                <option value="dry-run">dry-run（只评审）</option>
                <option value="normal">normal（评审 + 自动修复）</option>
              </select>
            </label>
            <label className="field-label">
              git ref
              <input
                type="text"
                value={form.ref}
                placeholder="HEAD"
                onChange={(event) => setField("ref", event.target.value)}
              />
            </label>
            <label className="field-label">
              轮数（1–20）
              <input
                type="text"
                inputMode="numeric"
                value={form.rounds}
                onChange={(event) => setField("rounds", event.target.value)}
              />
            </label>
            <label className="field-label">
              超时（秒，60–7200）
              <input
                type="text"
                inputMode="numeric"
                value={form.timeout}
                onChange={(event) => setField("timeout", event.target.value)}
              />
            </label>
            <div className="field-label" style={{ justifyContent: "flex-end" }}>
              <button className="btn primary" type="submit" disabled={saving}>
                {saving ? "保存中…" : existing ? "保存更新" : "登记计划"}
              </button>
            </div>
          </div>
          <p className="muted small">{describeScheduleMode(form.mode)}</p>
          {formErrors.length > 0 && (
            <div className="form-error" role="alert">
              {formErrors.map((message) => (
                <div key={message}>· {message}</div>
              ))}
            </div>
          )}
        </form>
      </section>

      <section className="panel">
        <h2>计划</h2>
        {jobs.length === 0 ? (
          <>
            <p className="muted">
              还没有登记任何计划。用上面的表单登记，或从终端执行：
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
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <JobRow
                    key={job.name}
                    job={job}
                    busy={busyJob === job.name}
                    onToggle={(target) => void toggleJob(target)}
                    onRemove={(target) => setPendingRemove(target)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>执行历史</h2>
        {orderedHistory.length === 0 ? (
          <p className="empty">
            还没有执行记录。调度器每次跑完一个计划都会在 <code>cron_history.jsonl</code> 里留一行。
          </p>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              最新在前，最多显示最近 {orderedHistory.length} 次；输出只保留尾部，完整日志见上面的日志文件。
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
                  {orderedHistory.map((entry, index) => (
                    <HistoryRow key={`${entry.started_at}-${entry.name}-${index}`} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {pendingReplace && (
        <ConfirmDialog
          title="把计划移到另一个目录？"
          confirmLabel="确认迁移"
          busy={saving}
          onConfirm={() => void save(pendingReplace, true)}
          onCancel={() => setPendingReplace(null)}
        >
          {existing
            ? `现有计划指向 ${existing.cwd || "(未知目录)"}，保存后会改为 ${pendingReplace.project_root || "当前项目根目录"}。`
            : "已有计划指向另一个目录，保存后会改为表单中的目录。"}
        </ConfirmDialog>
      )}

      {pendingRemove && (
        <ConfirmDialog
          title={`删除计划 ${pendingRemove.name}？`}
          confirmLabel="删除"
          danger
          busy={busyJob === pendingRemove.name}
          onConfirm={() => void removeJob(pendingRemove)}
          onCancel={() => setPendingRemove(null)}
        >
          删除后调度器不再执行它；历史记录仍保留在 <code>cron_history.jsonl</code>。
        </ConfirmDialog>
      )}
    </>
  );
}
