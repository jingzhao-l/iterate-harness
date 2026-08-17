// Checkpoints (design §17.3 P3) — view the persisted checkpoint, the latest
// report context and the interrupted flag, then trigger the controlled
// restore / clear operations behind a secondary confirmation dialog.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useWebUi } from "../store";
import type { CheckpointView, OperationResult } from "../types";

function renderCheckpoint(checkpoint: Record<string, unknown>): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  // Keys mirror the real payload persisted by iterate/checkpoint.py
  // `save_checkpoint` (round / new_findings / total_findings / per_dimension /
  // converged / input_tokens / output_tokens / cost_usd / mode).
  const keys = [
    "round",
    "mode",
    "new_findings",
    "total_findings",
    "per_dimension",
    "converged",
    "input_tokens",
    "output_tokens",
    "cost_usd",
  ];
  for (const key of keys) {
    const value = checkpoint[key];
    if (value === undefined || value === null) continue;
    const rendered =
      typeof value === "object" ? JSON.stringify(value) : String(value);
    rows.push([key, rendered]);
  }
  return rows;
}

export default function Checkpoints(): React.JSX.Element {
  const [view, setView] = useState<CheckpointView | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const pushToast = useWebUi((state) => state.pushToast);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setView(await api.checkpoints());
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const runOperation = async (
    operation: () => Promise<OperationResult>,
    onDone: () => void,
  ): Promise<void> => {
    setBusy(true);
    try {
      const result = await operation();
      pushToast(result.status === "ok" ? "success" : "error", result.message);
    } catch (error) {
      const message =
        error instanceof ApiError ? String(error.detail ?? error.message) : String(error);
      pushToast("error", message);
    } finally {
      setBusy(false);
      onDone();
      void load();
    }
  };

  const checkpointRows = view?.checkpoint ? renderCheckpoint(view.checkpoint) : [];

  return (
    <>
      <h1 className="page-title">检查点</h1>
      <p className="page-sub">断点状态与受控恢复 / 清除</p>

      {loading ? (
        <div className="loading-block">
          <span className="spinner" /> 加载检查点…
        </div>
      ) : !view ? (
        <section className="panel">
          <p className="empty">无法加载检查点状态</p>
        </section>
      ) : (
        <>
          <div className="cards">
            <div className="card">
              <div className="k">检查点</div>
              <div className="v small">{view.exists ? "存在" : "不存在"}</div>
            </div>
            <div className="card">
              <div className="k">上一轮次</div>
              <div className="v small">{view.last_report?.round ?? "—"}</div>
            </div>
            <div className="card">
              <div className="k">判定</div>
              <div className="v small">{view.last_report?.verdict ?? "—"}</div>
            </div>
            <div className="card">
              <div className="k">运行状态</div>
              <div className="v small">{view.interrupted ? "中断" : "正常"}</div>
            </div>
          </div>

          <section className="panel">
            <h2>检查点内容</h2>
            {view.exists && checkpointRows.length > 0 ? (
              <table className="data">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>值</th>
                  </tr>
                </thead>
                <tbody>
                  {checkpointRows.map(([key, value]) => (
                    <tr key={key}>
                      <td className="mono">{key}</td>
                      <td className="mono">{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="empty">暂无检查点</p>
            )}
          </section>

          <section className="panel">
            <h2>最近报告上下文</h2>
            {view.last_report ? (
              <p className="muted mono">
                {view.last_report.timestamp ?? "—"} · R{view.last_report.round ?? "—"} · 模式{" "}
                {view.last_report.mode ?? "—"} · {view.last_report.totalFindings ?? 0} findings
              </p>
            ) : (
              <p className="empty">暂无报告</p>
            )}
          </section>

          <section className="panel">
            <h2>操作</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              恢复会将检查点重新武装以供 <code>/iterate resume</code> 使用；清除会丢弃中断的
              检查点。两者均写入审计日志。
            </p>
            <div style={{ display: "flex", gap: 10 }}>
              <button
                className="btn primary"
                disabled={!view.exists || busy}
                onClick={() => setConfirmRestore(true)}
              >
                恢复检查点
              </button>
              <button
                className="btn danger"
                disabled={!view.exists || busy}
                onClick={() => setConfirmClear(true)}
              >
                清除检查点
              </button>
            </div>
          </section>
        </>
      )}

      {confirmRestore && (
        <ConfirmDialog
          title="恢复检查点"
          confirmLabel="确认恢复"
          busy={busy}
          onCancel={() => setConfirmRestore(false)}
          onConfirm={() => {
            void runOperation(() => api.restoreCheckpoint(), () => setConfirmRestore(false));
          }}
        >
          重新武装当前检查点，供下一次 <code>/iterate resume</code> 使用。此操作会写入审计日志。
        </ConfirmDialog>
      )}

      {confirmClear && (
        <ConfirmDialog
          title="清除检查点"
          confirmLabel="确认清除"
          danger
          busy={busy}
          onCancel={() => setConfirmClear(false)}
          onConfirm={() => {
            void runOperation(() => api.clearCheckpoint(), () => setConfirmClear(false));
          }}
        >
          将丢弃当前检查点。此操作不可撤销，并会写入审计日志。确定继续吗？
        </ConfirmDialog>
      )}
    </>
  );
}
