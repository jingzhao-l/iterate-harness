// App shell — sidebar navigation + routed pages + toast host + live SSE feed
// (design §17.3 P1–P7). The SSE subscription is started once per project root
// and torn down on unmount to avoid leaked EventSource connections. Every
// routed page is wrapped in an ErrorBoundary so a render crash never blanks
// the whole console. Keyboard shortcuts (design §17 UX):
//   - "g" then "d/r/w/c/b/p/g": jump to Dashboard / Runs / Workspaces /
//     Checkpoints / Budget / Config / Reports (g-buffer navigation)
//   - "/" or Cmd/Ctrl+K: toggle the human-in-the-loop chat panel

import { useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { subscribeToStatus, useWebUi } from "./store";
import ChatPanel from "./components/ChatPanel";
import ErrorBoundary from "./components/ErrorBoundary";
import ThemeToggle from "./components/ThemeToggle";
import BudgetRate from "./pages/BudgetRate";
import Checkpoints from "./pages/Checkpoints";
import ConfigPage from "./pages/ConfigPage";
import Dashboard from "./pages/Dashboard";
import Reports from "./pages/Reports";
import Runs from "./pages/Runs";
import Workspaces from "./pages/Workspaces";

const NAV_ITEMS = [
  { to: "/", label: "仪表盘", end: true },
  { to: "/runs", label: "迭代详情", end: false },
  { to: "/workspaces", label: "工作区", end: false },
  { to: "/checkpoints", label: "检查点", end: false },
  { to: "/budget", label: "预算与限流", end: false },
  { to: "/config", label: "配置", end: false },
  { to: "/reports", label: "报告", end: false },
];

// Single-key → route map for the "g <key>" jump shortcuts.
const G_BUFFER_ROUTES: Record<string, string> = {
  d: "/",
  r: "/runs",
  w: "/workspaces",
  c: "/checkpoints",
  b: "/budget",
  p: "/config",
  g: "/reports",
};

const CONN_LABELS: Record<string, string> = {
  connecting: "连接中…",
  connected: "实时流已连接",
  reconnecting: "重连中…",
  disconnected: "已断开",
};

// Sidebar control to select the project root the whole console operates on
// (design §17.5). Commits on blur / Enter so each keystroke doesn't tear down
// and rebuild the SSE subscription.
function ProjectRootInput(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const setProjectRoot = useWebUi((state) => state.setProjectRoot);
  const [value, setValue] = useState(projectRoot);

  useEffect(() => {
    setValue(projectRoot);
  }, [projectRoot]);

  const commit = (): void => {
    const trimmed = value.trim();
    if (trimmed !== projectRoot) setProjectRoot(trimmed);
  };

  return (
    <div className="project-root">
      <label htmlFor="project-root-input" className="project-root-label">
        项目根目录
      </label>
      <input
        id="project-root-input"
        type="text"
        value={value}
        placeholder="留空则自动探测"
        autoComplete="off"
        onChange={(event) => setValue(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            commit();
            (event.target as HTMLInputElement).blur();
          }
        }}
      />
    </div>
  );
}

export default function App(): React.JSX.Element {
  const projectRoot = useWebUi((state) => state.projectRoot);
  const toasts = useWebUi((state) => state.toasts);
  const dismissToast = useWebUi((state) => state.dismissToast);
  const connectionState = useWebUi((state) => state.connectionState);
  const navigate = useNavigate();
  const gBuffer = useRef<string | null>(null);
  const gBufferTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const unsubscribe = subscribeToStatus(projectRoot);
    return unsubscribe;
  }, [projectRoot]);

  // Global keyboard shortcuts.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      // Don't hijack typing in inputs/textareas/contenteditable.
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }

      // "/" or Cmd/Ctrl+K toggles the chat panel (dispatched to ChatPanel).
      if (
        event.key === "/" ||
        ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k")
      ) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("iterate:toggle-chat"));
        return;
      }

      // "g <key>" jump navigation.
      if (event.key.toLowerCase() === "g" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        if (gBufferTimer.current) clearTimeout(gBufferTimer.current);
        gBuffer.current = "g";
        gBufferTimer.current = setTimeout(() => {
          gBuffer.current = null;
        }, 800);
        return;
      }

      if (gBuffer.current === "g" && event.key.length === 1) {
        const route = G_BUFFER_ROUTES[event.key.toLowerCase()];
        if (route) {
          event.preventDefault();
          gBuffer.current = null;
          if (gBufferTimer.current) clearTimeout(gBufferTimer.current);
          navigate(route);
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="dot" />
          iterate-harness
        </div>
        <ProjectRootInput />
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="conn-row" title={CONN_LABELS[connectionState]}>
            <span className={`conn-dot ${connectionState}`} />
            {CONN_LABELS[connectionState]}
          </div>
          <ThemeToggle />
        </div>
      </aside>

      <main className="main">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/workspaces" element={<Workspaces />} />
            <Route path="/checkpoints" element={<Checkpoints />} />
            <Route path="/budget" element={<BudgetRate />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
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

      {/* Human-in-the-loop chat panel (design §18): fixed overlay available
          on every page; the harness center is the run, chat is a side panel.
          Wrapped in an ErrorBoundary like every routed page so a chat render
          crash can never blank the whole console. */}
      <ErrorBoundary>
        <ChatPanel />
      </ErrorBoundary>
    </div>
  );
}
