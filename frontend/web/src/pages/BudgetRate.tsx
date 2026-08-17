// Budget & Rate (design §17.3 P5) — cumulative token / USD meters against
// configured budgets, the rate-limit window and any exhausted dimensions.

import { useEffect } from "react";
import { useWebUi } from "../store";

function percent(used: number, budget: number | null): number {
  if (!budget || budget <= 0) return 0;
  return Math.min(100, Math.round((used / budget) * 100));
}

function meterClass(pct: number): string {
  if (pct >= 90) return "crit";
  if (pct >= 70) return "warn";
  return "ok";
}

function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return Math.round(value).toLocaleString("en-US");
}

export default function BudgetRate(): React.JSX.Element {
  const status = useWebUi((state) => state.status);
  const refreshStatus = useWebUi((state) => state.refreshStatus);
  const lastError = useWebUi((state) => state.lastError);
  const clearError = useWebUi((state) => state.clearError);

  useEffect(() => {
    if (!status) void refreshStatus();
  }, [status, refreshStatus]);

  if (!status) {
    // Loading failed (e.g. SSE + API both down): show an actionable error
    // state with a retry button instead of spinning forever.
    if (lastError) {
      return (
        <>
          <h1 className="page-title">预算与限流</h1>
          <section className="panel">
            <p className="muted">加载预算数据失败：{lastError}</p>
            <button
              className="btn primary"
              type="button"
              onClick={() => {
                clearError();
                void refreshStatus();
              }}
            >
              重试
            </button>
          </section>
        </>
      );
    }
    return (
      <div className="loading-block">
        <span className="spinner" /> 加载预算数据…
      </div>
    );
  }

  const budget = status.budget;
  const tokenPct = percent(budget.usedTokens, budget.tokenBudget);
  const usdPct = percent(budget.usedUsd, budget.budgetUsd);
  const hasBudgets = budget.tokenBudget !== null || budget.budgetUsd !== null;

  return (
    <>
      <h1 className="page-title">预算与限流</h1>
      <p className="page-sub">累计用量与 loop_policy 配置</p>

      <div className="cards">
        <div className="card">
          <div className="k">累计 Tokens</div>
          <div className="v small">{formatTokens(budget.usedTokens)}</div>
        </div>
        <div className="card">
          <div className="k">累计成本 (USD)</div>
          <div className="v small">${budget.usedUsd.toFixed(4)}</div>
        </div>
        <div className="card">
          <div className="k">Token 预算</div>
          <div className="v small">{budget.tokenBudget === null ? "未设置" : formatTokens(budget.tokenBudget)}</div>
        </div>
        <div className="card">
          <div className="k">美元预算</div>
          <div className="v small">{budget.budgetUsd === null ? "未设置" : `$${budget.budgetUsd.toFixed(2)}`}</div>
        </div>
        <div className="card">
          <div className="k">每分钟轮次上限</div>
          <div className="v small">{budget.maxTurnsPerMinute === null ? "未设置" : budget.maxTurnsPerMinute}</div>
        </div>
      </div>

      {hasBudgets && (
        <section className="panel">
          <h2>用量仪表</h2>
          {budget.tokenBudget !== null && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span className="muted">Tokens</span>
                <span className="mono">
                  {formatTokens(budget.usedTokens)} / {formatTokens(budget.tokenBudget)}（{tokenPct}%）
                </span>
              </div>
              <div className="meter-track">
                <div
                  className={`meter-fill ${meterClass(tokenPct)}`}
                  style={{ width: `${tokenPct}%` }}
                />
              </div>
            </div>
          )}
          {budget.budgetUsd !== null && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span className="muted">USD 预算</span>
                <span className="mono">
                  ${budget.usedUsd.toFixed(4)} / ${budget.budgetUsd.toFixed(2)}（{usdPct}%）
                </span>
              </div>
              <div className="meter-track">
                <div
                  className={`meter-fill ${meterClass(usdPct)}`}
                  style={{ width: `${usdPct}%` }}
                />
              </div>
            </div>
          )}
        </section>
      )}

      <section className="panel">
        <h2>熔断状态</h2>
        {budget.exhaustedDimensions.length === 0 ? (
          <p className="muted">无熔断维度，所有维度正常可用。</p>
        ) : (
          <>
            <p className="muted">以下维度已达预算上限，后续轮次将被跳过：</p>
            <table className="data">
              <thead>
                <tr>
                  <th>维度</th>
                </tr>
              </thead>
              <tbody>
                {budget.exhaustedDimensions.map((dimension) => (
                  <tr key={dimension}>
                    <td>
                      <span className="badge amber">{dimension}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <p className="muted" style={{ fontSize: 12 }}>
        数据来源：loop_policy 与 cost meter，随 SSE 流实时刷新。
      </p>
    </>
  );
}
