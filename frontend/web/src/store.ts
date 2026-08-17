// Zustand store for the WebUI: holds the live dashboard status, last error,
// and a toast queue. The status is refreshed by the SSE stream + periodic
// polling; all pages read from here instead of refetching on every render.

import { create } from "zustand";
import type { SseStatusPayload, StatusResponse } from "./types";
import { api } from "./api";

interface Toast {
  id: number;
  kind: "info" | "success" | "error";
  message: string;
}

interface WebUiState {
  projectRoot: string;
  status: StatusResponse | null;
  statusLoading: boolean;
  toasts: Toast[];
  lastError: string | null;
  // actions
  setProjectRoot: (root: string) => void;
  refreshStatus: () => Promise<void>;
  pushToast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: number) => void;
  clearError: () => void;
}

let toastSeq = 0;

export const useWebUi = create<WebUiState>((set, get) => ({
  projectRoot: "",
  status: null,
  statusLoading: false,
  toasts: [],
  lastError: null,

  setProjectRoot: (root) => set({ projectRoot: root }),

  refreshStatus: async () => {
    set({ statusLoading: true });
    try {
      const status = await api.status(get().projectRoot);
      set({ status, statusLoading: false, lastError: null });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      set({ statusLoading: false, lastError: message });
    }
  },

  pushToast: (kind, message) => {
    const id = ++toastSeq;
    set((state) => ({ toasts: [...state.toasts, { id, kind, message }] }));
    // Auto-dismiss after 4s.
    setTimeout(() => get().dismissToast(id), 4000);
  },

  dismissToast: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  clearError: () => set({ lastError: null }),
}));

// Hook to wire the SSE status stream into the store. Called once from App.
export function subscribeToStatus(projectRoot: string): () => void {
  const onStatus = (payload: unknown): void => {
    const delta = payload as SseStatusPayload;
    const current = useWebUi.getState().status;
    if (!current) return;
    // Merge live counters into the current status snapshot.
    useWebUi.setState({
      status: {
        ...current,
        entry_count: delta.entryCount ?? current.entry_count,
        latest_round: delta.latestRound ?? current.latest_round,
        budget: {
          ...current.budget,
          usedTokens: delta.totalTokens ?? current.budget.usedTokens,
          usedUsd: delta.totalCostUsd ?? current.budget.usedUsd,
        },
      },
    });
  };

  let source: EventSource | null = null;
  let closed = false;

  const connect = (): void => {
    if (closed) return;
    const url = `/api/v1/events?stream=all${
      projectRoot ? `&project_root=${encodeURIComponent(projectRoot)}` : ""
    }`;
    source = new EventSource(url);
    source.addEventListener("status", (event) => {
      try {
        onStatus(JSON.parse((event as MessageEvent).data));
      } catch {
        // ignore malformed SSE payloads
      }
    });
    source.onerror = (): void => {
      // EventSource auto-reconnects; schedule a poll fallback as well.
      source?.close();
      if (!closed) setTimeout(connect, 3000);
    };
  };

  connect();
  return () => {
    closed = true;
    source?.close();
  };
}
