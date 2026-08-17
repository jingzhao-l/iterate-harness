// WebUI entry point — mounts the hash-routed SPA (design §17.5).
// Hash routing keeps deep links working when the bundle is served by the
// FastAPI StaticFiles mount (no server-side SPA fallback required).

import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root mount point");
}

createRoot(container).render(
  <HashRouter>
    <App />
  </HashRouter>,
);
