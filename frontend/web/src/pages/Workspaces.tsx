// Workspaces (design §17.3 P4, §17.5) — lists the primary checkout plus every
// isolate worktree created by worktree_runtime when worktree_isolation is
// enabled. Each workspace shows git metadata, config info, and a "remove"
// action (with secondary confirmation) for stale worktrees.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { WorkspaceView } from "../types";
import { SkeletonTable } from "../components/Skeleton";
import { ConfirmDialog } from "../components/ConfirmDialog";

export default function Workspaces(): React.JSX.Element {
  const [workspaces, setWorkspaces] = useState<WorkspaceView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removeSlug, setRemoveSlug] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.workspaces();
      setWorkspaces(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRemove = async (): Promise<void> => {
    if (!removeSlug) return;
    setRemoving(true);
    try {
      await api.removeWorkspace(removeSlug);
      // Reload the list after removal.
      setRemoveSlug(null);
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRemoveSlug(null);
    } finally {
      setRemoving(false);
    }
  };

  const formatDate = (ts: number): string => {
    try {
      return new Date(ts * 1000).toLocaleString("zh-CN");
    } catch {
      return "—";
    }
  };

  return (
    <>
      <h1 className="page-title">工作区</h1>
      <p className="page-sub">
        主检出版本与隔离 worktree 列表。worktree 隔离启用时，每轮迭代在独立沙箱中运行。
      </p>

      <button className="btn" onClick={load} disabled={loading} style={{ marginBottom: 12 }}>
        刷新
      </button>

      {loading ? (
        <section className="panel">
          <h2>工作区列表</h2>
          <SkeletonTable rows={4} />
        </section>
      ) : error ? (
        <section className="panel">
          <h2>工作区列表</h2>
          <p className="empty">加载失败：{error}</p>
        </section>
      ) : workspaces.length === 0 ? (
        <section className="panel">
          <h2>工作区列表</h2>
          <p className="empty">暂无工作区</p>
        </section>
      ) : (
        <section className="panel">
          <h2>工作区列表（共 {workspaces.length} 个）</h2>
          <table className="data">
            <thead>
              <tr>
                <th>名称</th>
                <th>类型</th>
                <th>路径</th>
                <th>分支 / HEAD</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.map((ws) => (
                <tr key={ws.name}>
                  <td className="mono">{ws.name}</td>
                  <td>
                    <span className={`badge ${ws.kind === "primary" ? "green" : "neutral"}`}>
                      {ws.kind === "primary" ? "主目录" : "worktree"}
                    </span>
                  </td>
                  <td className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>
                    {ws.path}
                  </td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {ws.detail.branch ?? "—"}
                    <br />
                    <span className="muted">{ws.detail.head ?? ""}</span>
                    {ws.detail.created_at ? (
                      <>
                        <br />
                        <span className="muted">创建于 {formatDate(ws.detail.created_at)}</span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    {ws.detail.dirty && <span className="badge amber" style={{ marginRight: 4 }}>未提交</span>}
                    {!ws.active && <span className="badge gray">已过期</span>}
                    {ws.detail.isolationEnabled && <span className="badge neutral">隔离</span>}
                    {ws.detail.round !== undefined && ws.detail.round > 0 && (
                      <span className="badge green">R{ws.detail.round}</span>
                    )}
                  </td>
                  <td>
                    {ws.kind === "worktree" && !ws.active && (
                      <button
                        className="btn danger"
                        style={{ padding: "4px 10px", fontSize: 12 }}
                        disabled={removing}
                        onClick={() => setRemoveSlug(ws.detail.slug ?? ws.name)}
                      >
                        删除
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Secondary confirmation dialog for worktree removal */}
      {removeSlug && (
        <ConfirmDialog
          title="删除工作区"
          confirmLabel="删除"
          danger
          busy={removing}
          onConfirm={() => void handleRemove()}
          onCancel={() => setRemoveSlug(null)}
        >
          确定要删除 worktree「{removeSlug}」吗？此操作不可撤销。
        </ConfirmDialog>
      )}
    </>
  );
}