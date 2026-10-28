// Recipe gate (design §17.3 P9) — the ④ answer a round publishes when it has
// measured an invariant verdict ("may the recipe proceed to re-verification?").
//
// `ih iterate gate` has been able to read this out of the decision log since the
// GlassPane hand-off; the console could not. This page closes that disconnect:
// the latest answer, the refusal and its remedy, and every round that published
// one — plus the exact CLI text so the two surfaces can be compared. Read-only.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { gateHeadline, newestFirst, recordKey, shortDigest } from "../gate";
import { useWebUi } from "../store";
import type { GateRecord, GateView } from "../types";

function shortTime(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 19).replace("T", " ");
}

function verdictTone(verdict: string): string {
  if (verdict === "pass") return "green";
  if (verdict === "fail") return "severity-critical";
  return "neutral";
}

function GateDetail({ record }: { record: GateRecord }): React.JSX.Element {
  const headline = gateHeadline(record);
  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="k">结论</div>
          <div className={`v small direction-${headline.tone}`}>{headline.label}</div>
          <div className="muted card-note">{headline.detail}</div>
        </div>
        <div className="card">
          <div className="k">不变量判定</div>
          <div className="v small">
            <span className={`badge ${verdictTone(record.invariant_verdict)}`}>
              {record.invariant_verdict}
            </span>
          </div>
          <div className="muted card-note">这一步先于配方与修复证据被检查</div>
        </div>
        <div className="card">
          <div className="k">轮次 / 时间</div>
          <div className="v small mono">{record.round}</div>
          <div className="muted card-note mono">{shortTime(record.timestamp)}</div>
        </div>
        <div className="card">
          <div className="k">配方摘要</div>
          <div className="v small mono" title={record.recipe_digest ?? undefined}>
            {shortDigest(record.recipe_digest)}
          </div>
          <div className="muted card-note">这一轮实际读到的配方版本</div>
        </div>
      </div>

      <section className="panel">
        <h2>配置的步骤</h2>
        {record.steps.length === 0 ? (
          <p className="empty small">这一轮没有记录步骤。</p>
        ) : (
          <div className="chip-row">
            {record.steps.map((step) => (
              <span className="chip mono" key={step}>
                {step}
              </span>
            ))}
          </div>
        )}
      </section>

      {record.fix_proof && (
        <section className="panel">
          <h2>修复证据来源</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            闸门只认链校验通过、且带 operation id 的日志行；“修复完成”是这条证据说的，不是本轮自述。
          </p>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                <tr>
                  <th>operation</th>
                  <td className="mono">{record.fix_proof.operation_id || "—"}</td>
                </tr>
                <tr>
                  <th>entry</th>
                  <td className="mono">{record.fix_proof.entry_id || "—"}</td>
                </tr>
                <tr>
                  <th>结果</th>
                  <td>{record.fix_proof.outcome || "—"}</td>
                </tr>
                <tr>
                  <th>来源</th>
                  <td>
                    {record.fix_proof.ledger_source || "—"}
                    <span className="mono muted"> · {record.fix_proof.ledger || "—"}</span>
                  </td>
                </tr>
                <tr>
                  <th>记录时间</th>
                  <td className="mono">{record.fix_proof.created_at || "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      {record.refusal ? (
        <section className="panel">
          <h2>
            拒绝原因 <span className="badge severity-critical">{record.refusal.code || "—"}</span>
          </h2>
          <p>{record.refusal.message || "这一轮没有给出说明。"}</p>
          <p className="muted" style={{ marginBottom: 4 }}>
            可执行的下一步：
          </p>
          <pre className="cmd-hint">{record.refusal.remedy || "重新运行这一轮并检查不变量配置。"}</pre>
        </section>
      ) : (
        <section className="panel">
          <h2>拒绝原因</h2>
          <p className="empty small">这一轮没有被拒绝。</p>
        </section>
      )}
    </>
  );
}

export default function Gate(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const logRevision = useWebUi((state) => state.logRevision);
  const pushToast = useWebUi((state) => state.pushToast);
  const [view, setView] = useState<GateView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      setView(await api.gate(projectRoot, 200));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectRoot]);

  useEffect(() => {
    void load();
  }, [load]);

  // A finished round appends to the decision log; refetch so its answer shows
  // without a manual reload (the store batches SSE-driven revisions).
  useEffect(() => {
    if (logRevision > 0) void load();
  }, [logRevision, load]);

  const records = useMemo(() => newestFirst(view?.history ?? []), [view]);
  const selected = useMemo(() => {
    const chosen = records.find((record) => recordKey(record) === selectedKey);
    return chosen ?? view?.latest ?? null;
  }, [records, selectedKey, view]);

  const copy = async (): Promise<void> => {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(view?.rendered ?? "");
      pushToast("success", "已复制判定闸输出");
    } catch {
      pushToast("error", "复制失败：浏览器拒绝了剪贴板访问");
    }
  };

  const header = (
    <div className="page-head">
      <div>
        <h1 className="page-title">判定闸</h1>
        <p className="page-sub">“这一轮可以进入复验吗？”——最近一次回答、拒绝原因与全部历史</p>
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
          <span className="spinner" /> 读取判定闸记录…
        </div>
      </>
    );
  }

  if (error && !view) {
    return (
      <>
        {header}
        <section className="panel">
          <p className="empty">读取判定闸失败：{error}</p>
          <button className="btn primary" onClick={() => void load()}>
            重试
          </button>
        </section>
      </>
    );
  }

  if (!view || !view.has_gate || !selected) {
    return (
      <>
        {header}
        <section className="panel">
          <h2>还没有记录过判定闸</h2>
          <p className="muted">
            这一页读取项目自己的决策日志（<code>.iterate/decisions.jsonl</code>）。只有测量过不变量判定的一轮
            才会发布这个答案；没有记录代表“没人问过”，<strong>不代表通过</strong>。
          </p>
          <pre className="cmd-hint">ih iterate run --rounds 3</pre>
          <p className="muted">
            跑完一轮后，同一个答案也能在终端读到：<code>ih iterate gate</code>。
          </p>
        </section>
      </>
    );
  }

  return (
    <>
      {header}

      {error && <p className="empty">刷新失败（显示上次数据）：{error}</p>}

      <p className="muted" style={{ marginTop: 0 }}>
        共记录 {view.total} 次判定，当前展示第 {selected.round} 轮（{shortTime(selected.timestamp)}）。
        {records.length < view.total ? ` 仅显示最近 ${records.length} 次。` : ""}
      </p>

      <GateDetail record={selected} />

      <section className="panel">
        <h2>判定历史</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          点一行查看那一轮的完整回答；最新在前。拒绝不等于失败终局——按“可执行的下一步”修完再跑一轮即可。
        </p>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>轮次</th>
                <th>时间</th>
                <th>结论</th>
                <th>不变量</th>
                <th>配方</th>
                <th>拒绝码</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => {
                const key = recordKey(record);
                return (
                  <tr
                    key={key}
                    className={key === recordKey(selected) ? "row-selected" : ""}
                    role="button"
                    tabIndex={0}
                    aria-pressed={key === recordKey(selected)}
                    style={{ cursor: "pointer" }}
                    onClick={() => setSelectedKey(key)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedKey(key);
                      }
                    }}
                  >
                    <td className="mono">{record.round}</td>
                    <td className="mono">{shortTime(record.timestamp)}</td>
                    <td>
                      <span className={`badge ${record.allowed ? "green" : "severity-critical"}`}>
                        {record.allowed ? "允许" : "拒绝"}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${verdictTone(record.invariant_verdict)}`}>
                        {record.invariant_verdict}
                      </span>
                    </td>
                    <td className="mono" title={record.recipe_digest ?? undefined}>
                      {shortDigest(record.recipe_digest)}
                    </td>
                    <td className="mono">{record.refusal?.code ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>终端原文</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          与 <code>ih iterate gate</code> 的输出逐字一致，便于对照。
        </p>
        <pre className="cmd-hint">{view.rendered}</pre>
        <button className="btn" onClick={() => void copy()}>
          复制原文
        </button>
      </section>
    </>
  );
}
