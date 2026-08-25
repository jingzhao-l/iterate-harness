/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// WebUI frontend build config.
// - base: "./" so the built assets resolve relative to the mounted path
//   inside the FastAPI StaticFiles mount (no hard-coded absolute /assets/).
// - outDir: dist (force-included into the wheel by pyproject force-include).
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // During `npm run dev`, proxy API + SSE to the FastAPI backend.
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
    },
  },
  test: {
    // Unit tests cover pure logic only (no DOM), so the node environment is
    // enough; the API layer is mocked per test file.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
