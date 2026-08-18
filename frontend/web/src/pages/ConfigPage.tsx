// Config (design §17.3 P6) — read-only effective config view, provider list
// with redacted credentials, and a validated write-back editor (backup +
// rollback + secondary confirmation). YAML is edited client-side via js-yaml
// and submitted as a JSON object to PUT /api/v1/config.

import { useCallback, useEffect, useState } from "react";
import { load as yamlLoad, dump as yamlDump } from "js-yaml";
import { api, ApiError } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useWebUi } from "../store";
import type { ConfigView } from "../types";

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// Redacted presentation for provider credentials (backend field: api_key).
// Never reveals the key body; only reports its length so the operator can
// tell a filled field from an empty one at a glance.
function renderSecret(value: unknown): string {
  if (typeof value !== "string" || value.length === 0) return "—";
  return `••••••••（${value.length} 字符，已脱敏）`;
}

export default function ConfigPage(): React.JSX.Element {
  const [view, setView] = useState<ConfigView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [confirmSave, setConfirmSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const pushToast = useWebUi((state) => state.pushToast);
  const projectRoot = useWebUi((state) => state.projectRoot);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setLoadError(null);
    try {
      const config = await api.config(projectRoot);
      setView(config);
      setDraft(yamlDump(config.raw, { noRefs: true, lineWidth: 120 }));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      pushToast("error", error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [pushToast, projectRoot]);

  useEffect(() => {
    void load();
  }, [load]);

  const onDraftChange = (value: string): void => {
    setDraft(value);
    setDirty(value !== yamlDump(view?.raw ?? {}, { noRefs: true, lineWidth: 120 }));
    setParseError(null);
    try {
      const parsed = yamlLoad(value);
      if (parsed !== null && typeof parsed !== "object") {
        setParseError("配置必须是 YAML 映射（键值对）");
      } else if (Array.isArray(parsed)) {
        setParseError("配置不能是 YAML 数组");
      }
    } catch (error) {
      setParseError(error instanceof Error ? error.message : String(error));
    }
  };

  const handleSave = async (): Promise<void> => {
    setBusy(true);
    try {
      const parsed = yamlLoad(draft);
      const result = await api.saveConfig((parsed ?? {}) as Record<string, unknown>, projectRoot);
      pushToast(result.status === "ok" ? "success" : "error", result.message);
      if (result.status === "ok") {
        const refreshed = await api.config(projectRoot);
        setView(refreshed);
        setDraft(yamlDump(refreshed.raw, { noRefs: true, lineWidth: 120 }));
        setDirty(false);
      }
    } catch (error) {
      const message =
        error instanceof ApiError ? String(error.detail ?? error.message) : String(error);
      pushToast("error", message);
    } finally {
      setBusy(false);
      setConfirmSave(false);
    }
  };

  const effectiveRows = view
    ? [
        ["goal", renderValue(view.effective.goal)],
        ["maxRounds", renderValue(view.effective.maxRounds)],
        ["language", renderValue(view.effective.language)],
        ["dimensions", renderValue(view.effective.dimensions)],
        ["review.scope", renderValue((view.effective.review as Record<string, unknown> | undefined)?.scope)],
        ["tokenBudget", renderValue(view.effective.tokenBudget)],
        ["budgetUsd", renderValue(view.effective.budgetUsd)],
        ["maxTurnsPerMinute", renderValue(view.effective.maxTurnsPerMinute)],
        ["worktreeIsolation", renderValue(view.effective.worktreeIsolation)],
      ]
    : [];

  const providerEntries = view
    ? Object.entries(view.providers ?? {}).sort(([a], [b]) => a.localeCompare(b))
    : [];

  return (
    <>
      <h1 className="page-title">配置</h1>
      <p className="page-sub">iterate.config.yaml 只读预览 + 受控编辑</p>

      {loading ? (
        <div className="loading-block">
          <span className="spinner" /> 加载配置…
        </div>
      ) : !view ? (
        <section className="panel">
          <p className="muted">无法加载配置{loadError ? `：${loadError}` : ""}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      ) : (
        <>
          <section className="panel">
            <h2>生效配置摘要</h2>
            <table className="data">
              <tbody>
                {effectiveRows.map(([key, value]) => (
                  <tr key={key}>
                    <td className="mono" style={{ width: 220 }}>
                      {key}
                    </td>
                    <td className="mono">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel">
            <h2>Provider 与 BYOK</h2>
            {providerEntries.length === 0 ? (
              <p className="empty">未配置 provider 档案</p>
            ) : (
              <table className="data">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>密钥</th>
                    <th>参数</th>
                  </tr>
                </thead>
                <tbody>
                  {providerEntries.map(([name, profile]) => (
                    <tr key={name}>
                      <td>
                        <span className="mono">{name}</span>{" "}
                        {view.active_profile === name && <span className="badge green">active</span>}
                      </td>
                      <td className="mono">{renderSecret((profile as Record<string, unknown>).api_key)}</td>
                      <td className="muted">
                        {Object.entries(profile as Record<string, unknown>)
                          .filter(([key]) => key !== "api_key")
                          .map(([key, value]) => `${key}=${renderValue(value)}`)
                          .join(" · ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <h2>编辑 iterate.config.yaml</h2>
            <p className="muted" style={{ marginTop: 0 }}>
              保存前会进行校验并备份旧配置（<code>.bak.webui</code>）；写入失败自动回滚。API
              密钥等敏感字段在显示时已脱敏。
            </p>
            <textarea
              className="code-input"
              value={draft}
              spellCheck={false}
              onChange={(event) => onDraftChange(event.target.value)}
            />
            {parseError && (
              <p className="form-error">YAML 解析错误：{parseError}</p>
            )}
            <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
              <button
                className="btn primary"
                disabled={!dirty || busy || parseError !== null}
                onClick={() => setConfirmSave(true)}
              >
                保存配置
              </button>
              <button
                className="btn"
                disabled={!dirty || busy}
                onClick={() => {
                  setDraft(yamlDump(view.raw, { noRefs: true, lineWidth: 120 }));
                  setDirty(false);
                  setParseError(null);
                }}
              >
                撤销修改
              </button>
            </div>
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              配置路径：<span className="mono">{view.path}</span>（来源 {view.source}）
            </p>
          </section>
        </>
      )}

      {confirmSave && (
        <ConfirmDialog
          title="保存配置"
          confirmLabel="确认写入"
          busy={busy}
          onCancel={() => setConfirmSave(false)}
          onConfirm={() => {
            void handleSave();
          }}
        >
          将校验并写回 iterate.config.yaml（旧配置备份为 .bak.webui）。此操作会写入审计日志。
        </ConfirmDialog>
      )}
    </>
  );
}
