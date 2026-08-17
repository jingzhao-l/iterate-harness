// Reports (design §17.3 P7) — list generated report artifacts and preview
// their HTML inline (sandboxed iframe) with a replay entry point.

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useWebUi } from "../store";
import type { ReportPreview, ReportView } from "../types";

function formatSize(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${bytes} B`;
}

export default function Reports(): React.JSX.Element {
  const [reports, setReports] = useState<ReportView[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ReportPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const pushToast = useWebUi((state) => state.pushToast);

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      setReports(await api.reports());
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  useEffect(() => {
    void loadReports();
  }, [loadReports]);

  const openPreview = useCallback(
    async (name: string): Promise<void> => {
      setPreviewLoading(true);
      setSelected(null);
      try {
        const preview = await api.reportPreview(name);
        setSelected(preview);
        setSearchParams({ name }, { replace: true });
      } catch (error) {
        pushToast("error", error instanceof Error ? error.message : String(error));
      } finally {
        setPreviewLoading(false);
      }
    },
    [pushToast, setSearchParams],
  );

  // Auto-open a report when arriving with ?name= (e.g. from the dashboard).
  useEffect(() => {
    const requested = searchParams.get("name");
    if (requested && reports.length > 0 && !selected && !previewLoading) {
      void openPreview(requested);
    }
  }, [reports, searchParams, selected, previewLoading, openPreview]);

  return (
    <>
      <h1 className="page-title">报告</h1>
      <p className="page-sub">生成的 HTML / CSV 报告产物与内嵌预览</p>

      {loading ? (
        <div className="loading-block">
          <span className="spinner" /> 加载报告列表…
        </div>
      ) : reports.length === 0 ? (
        <section className="panel">
          <p className="empty">暂无报告产物</p>
        </section>
      ) : (
        <div className="reports-layout">
          <section className="panel reports-side">
            <h2>产物列表</h2>
            <table className="data">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>大小</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => (
                  <tr key={report.name} className={selected?.name === report.name ? "row-active" : ""}>
                    <td>
                      <button
                        className="link-btn"
                        onClick={() => void openPreview(report.name)}
                      >
                        {report.name}
                      </button>
                    </td>
                    <td className="mono">{formatSize(report.size)}</td>
                    <td className="mono muted">
                      {report.modified ? report.modified.slice(5, 19) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              报告文件位于项目 <code>.iterate</code> 目录，受路径白名单保护。
            </p>
          </section>

          <section className="panel reports-main">
            <h2>预览</h2>
            {previewLoading ? (
              <div className="loading-block">
                <span className="spinner" /> 加载预览…
              </div>
            ) : selected ? (
              <>
                <p className="muted mono" style={{ marginTop: 0 }}>
                  {selected.name} · {formatSize(selected.size)}
                </p>
                <iframe
                  className="report-frame"
                  title={selected.name}
                  sandbox="allow-same-origin allow-scripts"
                  srcDoc={selected.content}
                />
              </>
            ) : (
              <p className="empty">从左侧选择一个报告以预览</p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
