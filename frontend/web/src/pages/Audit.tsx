// Audit trail (design §17.3 P13) — the read side of the append-only
// .iterate/web-audit.jsonl journal that every mutating console operation
// (config save, checkpoint restore/clear, schedule upsert/toggle/delete,
// findings-triage, agent shutdown, workspace removal, run control) writes.
// Before this page the operator could only see the ten newest lines on the
// dashboard; now the whole trail is filterable, paged and exportable.
// Read-only: never writes the journal or any project state.

import { useCallback, useEffect, useMemo, useState } from "react";
import { downloadAuditExport, fetchAudit } from "../api";
import { formatTimestamp, summaryText, toIsoLocal } from "../audit";
import { useWebUi } from "../store";
import type { AuditEntry, AuditExportFormat, AuditView } from "../types";

const PAGE_SIZE = 50;

function ExportButton({
  format,
  label,
  query,
  projectRoot,
}: {
  format: AuditExportFormat;
  label: string;
  query: { action?: string; target?: string; since?: string };
  projectRoot: string;
}): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pushToast = useWebUi((state) => state.pushToast);

  const download = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const result = await downloadAuditExport(projectRoot, format, query);
      const url = URL.createObjectURL(
        new Blob([result.text], { type: result.contentType }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      pushToast("success", `已导出 ${result.filename}`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(message);
      pushToast("error", message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button className="btn small" onClick={() => void download()} disabled={busy}>
        {busy ? "导出中…" : label}
      </button>
      {error && <span className="muted">（{error}）</span>}
    </>
  );
}

function EntryRow({ entry }: { entry: AuditEntry }): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const summary = summaryText(entry);
  const copy = (): void => {
    void navigator.clipboard?.writeText(summary).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <tr className="audit-row">
      <td className="mono" style={{ whiteSpace: "nowrap" }}>
        {formatTimestamp(entry.timestamp)}
      </td>
      <td className="mono strong">{entry.action}</td>
      <td className="mono">{entry.target || "—"}</td>
      <td className="audit-summary">
        {summary ? (
          <>
            <button
              className="link-btn"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
            >
              {expanded ? "收起" : "详情"}
            </button>
            {expanded && (
              <div className="audit-json">
                <pre>{summary}</pre>
                <button className="btn small" onClick={copy}>
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
            )}
          </>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
    </tr>
  );
}

export default function Audit(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const [view, setView] = useState<AuditView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");
  const [targetFilter, setTargetFilter] = useState("");
  const [sinceFilter, setSinceFilter] = useState("");
  const [offset, setOffset] = useState(0);

  const query = useMemo(
    () => ({
      action: actionFilter,
      target: targetFilter.trim(),
      since: toIsoLocal(sinceFilter),
    }),
    [actionFilter, targetFilter, sinceFilter],
  );

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await fetchAudit(projectRoot, { ...query, offset, limit: PAGE_SIZE }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectRoot, query, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const actions = view?.actions ?? [];
  const filtersChanged =
    actionFilter !== "" || targetFilter.trim() !== "" || sinceFilter !== "";

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">审计</h1>
          <p className="page-sub">
            控制台的每一次写操作：时间、操作、目标与参数（与 `ih audit` 同源）
          </p>
        </div>
        <div className="btn-row">
          <ExportButton format="md" label="导出 Markdown" query={query} projectRoot={projectRoot} />
          <ExportButton format="csv" label="导出 CSV" query={query} projectRoot={projectRoot} />
          <ExportButton format="json" label="导出 JSON" query={query} projectRoot={projectRoot} />
        </div>
      </div>

      <section className="panel filter-bar">
        <label className="field-label">
          操作
          <select
            value={actionFilter}
            onChange={(event) => {
              setActionFilter(event.target.value);
              setOffset(0);
            }}
            aria-label="按操作前缀筛选"
          >
            <option value="">全部操作</option>
            {actions.map((action) => (
              <option key={action} value={action}>
                {action}
              </option>
            ))}
          </select>
        </label>
        <label className="field-label">
          目标
          <input
            type="search"
            className="search-input"
            placeholder="按目标包含筛选"
            value={targetFilter}
            onChange={(event) => {
              setTargetFilter(event.target.value);
              setOffset(0);
            }}
            aria-label="按目标筛选"
          />
        </label>
        <label className="field-label">
          起始时间
          <input
            type="datetime-local"
            value={sinceFilter}
            onChange={(event) => {
              setSinceFilter(event.target.value);
              setOffset(0);
            }}
            aria-label="按起始时间筛选"
          />
        </label>
        <button
          className="btn"
          onClick={() => {
            setActionFilter("");
            setTargetFilter("");
            setSinceFilter("");
            setOffset(0);
          }}
          disabled={!filtersChanged}
        >
          清除筛选
        </button>
      </section>

      <div className="cards">
        <div className="card">
          <div className="k">匹配</div>
          <div className="v">{view?.total ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">本页</div>
          <div className="v">{view?.returned ?? "—"}</div>
        </div>
        <div className="card">
          <div className="k">起始偏移</div>
          <div className="v small">{view?.offset ?? 0}</div>
        </div>
        <div className="card">
          <div className="k">操作类别</div>
          <div className="v small">{view?.actions.length ?? 0}</div>
        </div>
      </div>

      {loading ? (
        <div className="loading-block">
          <span className="spinner" /> 加载审计记录…
        </div>
      ) : error ? (
        <section className="panel">
          <p className="empty">加载审计记录失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      ) : !view || view.entries.length === 0 ? (
        <section className="panel">
          <p className="empty">
            {filtersChanged ? "没有匹配当前筛选条件的审计记录。" : "暂无写操作记录。"}
          </p>
          <p className="muted" style={{ fontSize: 12 }}>
            配置保存、检查点恢复/清除、定时任务变更、分诊决定、子代理停止等操作都会记录在这里。
          </p>
        </section>
      ) : (
        <>
          <section className="panel">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作</th>
                    <th>目标</th>
                    <th>参数</th>
                  </tr>
                </thead>
                <tbody>
                  {view.entries.map((entry, index) => (
                    <EntryRow key={`${entry.timestamp}-${entry.action}-${entry.target}-${index}`} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button
                className="btn small"
                onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
                disabled={offset === 0}
              >
                ‹ 上一页
              </button>
              <span className="muted">
                显示 {offset + 1}–{offset + view.entries.length} / 共 {view.total} 条
              </span>
              <button
                className="btn small"
                onClick={() => setOffset((value) => value + PAGE_SIZE)}
                disabled={!view.truncated}
              >
                下一页 ›
              </button>
            </div>
          </section>
          <p className="muted" style={{ fontSize: 12 }}>
            此页只读，不会写入任何项目状态。筛选与导出共用同一份过滤条件。
          </p>
        </>
      )}
    </>
  );
}
