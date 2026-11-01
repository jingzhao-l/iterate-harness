// Environment self-check (design §17.3 P11) — the console's projection of
// `ih doctor`. Before this page, "is this machine ready to iterate?" could
// only be answered by the REPL's /doctor slash command; CI and the browser
// had no surface. The page renders the same report the CLI prints, each
// check with an executable remedy the operator can copy, and a headline that
// leads with the worst condition. Read-only: never writes project state.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  STATUS_LABELS,
  doctorHeadline,
  groupChecks,
  statusTone,
} from "../doctor";
import { useWebUi } from "../store";
import type { DoctorCheck, DoctorView } from "../types";

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function CheckRow({ check }: { check: DoctorCheck }): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard?.writeText(check.remedy).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <tr className={check.status !== "ok" ? "row-live" : "row-dim"}>
      <td>
        <span className={`badge ${statusTone(check.status)}`}>
          {STATUS_LABELS[check.status]}
        </span>
      </td>
      <td className="mono strong">{check.id}</td>
      <td>{check.title}</td>
      <td className="doctor-detail" title={check.detail}>
        {check.detail || "—"}
      </td>
      <td>
        {check.remedy ? (
          <div className="remedy-row">
            <code className="remedy">{check.remedy}</code>
            <button
              className="btn small"
              onClick={copy}
              aria-label="复制修复命令"
            >
              {copied ? "已复制" : "复制"}
            </button>
          </div>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

export default function Doctor(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const [view, setView] = useState<DoctorView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [onlyAttention, setOnlyAttention] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.doctor(projectRoot));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectRoot]);

  useEffect(() => {
    void load();
  }, [load]);

  const headline = doctorHeadline(view);
  const checks = view ? groupChecks(view.checks) : [];
  const visible = onlyAttention
    ? checks.filter((check) => check.status !== "ok")
    : checks;

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">自检</h1>
        <p className="page-sub">
          本机环境就绪度：依赖、认证、配置、项目状态与沙箱（与 `ih doctor` 同源）
        </p>
      </div>
      <button className="btn" onClick={() => void load()} disabled={loading}>
        {loading ? "检查中…" : "重新自检"}
      </button>
    </div>
  );

  if (loading && !view) {
    return (
      <>
        {header}
        <div className="loading-block">
          <span className="spinner" /> 运行自检…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">自检失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!view) return <>{header}</>;

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次结果）：{error}</p>}

      <div className="cards">
        <div className="card">
          <div className="k">结论</div>
          <div className={`v small direction-${headline.tone}`}>
            {headline.label}
          </div>
          <div className="muted card-note">{headline.detail}</div>
        </div>
        <div className="card">
          <div className="k">正常</div>
          <div className="v small mono">{view.summary.ok ?? 0}</div>
          <div className="muted card-note">检查通过</div>
        </div>
        <div className="card">
          <div className="k">告警</div>
          <div className="v small mono">{view.summary.warn ?? 0}</div>
          <div className="muted card-note">可运行，建议处理</div>
        </div>
        <div className="card">
          <div className="k">失败</div>
          <div className="v small mono">{view.summary.fail ?? 0}</div>
          <div className="muted card-note">阻塞性问题</div>
        </div>
        <div className="card">
          <div className="k">项目根</div>
          <div className="v small mono" title={view.project_root}>
            {view.project_root}
          </div>
          <div className="muted card-note">检查时间 {shortTime(view.generated_at)}</div>
        </div>
      </div>

      <div className="filter-row">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={onlyAttention}
            onChange={(event) => setOnlyAttention(event.target.checked)}
          />
          只看需要处理（失败 + 告警）
        </label>
      </div>

      {visible.length === 0 ? (
        <section className="panel">
          <p className="empty">没有需要处理的项目——环境就绪。</p>
        </section>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>状态</th>
                <th>标识</th>
                <th>检查项</th>
                <th>详情</th>
                <th>修复方法</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((check) => (
                <CheckRow key={check.id} check={check} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="muted">
        此页只读，不会修改任何项目或机器状态。命令行等价物：`ih doctor`（可加
        `--json` / `--strict` 用于 CI）。
      </p>
    </>
  );
}
