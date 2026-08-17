// App shell — sidebar navigation + routed pages + toast host + live SSE feed
// (design §17.3 P1–P7). The SSE subscription is started once per project root
// and torn down on unmount to avoid leaked EventSource connections.

import { useEffect } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { subscribeToStatus, useWebUi } from "./store";
import BudgetRate from "./pages/BudgetRate";
import Checkpoints from "./pages/Checkpoints";
import ConfigPage from "./pages/ConfigPage";
import Dashboard from "./pages/Dashboard";
import Reports from "./pages/Reports";
import Runs from "./pages/Runs";

const NAV_ITEMS = [
  { to: "/", label: "仪表盘", end: true },
  { to: "/runs", label: "迭代详情", end: false },
  { to: "/checkpoints", label: "检查点", end: false },
  { to: "/budget", label: "预算与限流", end: false },
  { to: "/config", label: "配置", end: false },
  { to: "/reports", label: "报告", end: false },
];

export default function App(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const toasts = useWebUi((state) => state.toasts);
  const dismissToast = useWebUi((state) => state.dismissToast);

  useEffect(() => {
    const unsubscribe = subscribeToStatus(projectRoot);
    return unsubscribe;
  }, [projectRoot]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="dot" />
          iterate-harness
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/checkpoints" element={<Checkpoints />} />
          <Route path="/budget" element={<BudgetRate />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <div className="toasts">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast ${toast.kind}`}
            onClick={() => dismissToast(toast.id)}
            role="status"
          >
            {toast.message}
          </div>
        ))}
      </div>
    </div>
  );
}
