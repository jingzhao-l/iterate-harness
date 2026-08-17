// Zustand store for the WebUI: holds the live dashboard status, last error,
// the chat / human-in-the-loop state, and a toast queue. The dashboard status
// is refreshed by the SSE stream + periodic polling; the chat state is fed by
// the live hub events (chat-message / run-state / progress-update) and the
// /chat/* REST endpoints. All pages read from here instead of refetching.

import { create } from "zustand";
import type {
  ChatMessage,
  ChatRunStatus,
  ProgressUpdateEvent,
  RunStateEvent,
  SseStatusPayload,
  StatusResponse,
} from "./types";
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
  // Live SSE connection state (design §17 UX: visible indicator in the sidebar).
  connectionState: "connecting" | "connected" | "reconnecting" | "disconnected";
  // Chat / human-in-the-loop (design §18).
  chatMessages: ChatMessage[];
  chatStatus: ChatRunStatus | null;
  chatLoading: boolean;
  chatError: string | null;
  chatSending: boolean;
  // actions
  setProjectRoot: (root: string) => void;
  refreshStatus: () => Promise<void>;
  pushToast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: number) => void;
  clearError: () => void;
  setConnectionState: (
    state: "connecting" | "connected" | "reconnecting" | "disconnected",
  ) => void;
  setChatStatus: (status: ChatRunStatus | null) => void;
  patchChatStatus: (patch: Partial<ChatRunStatus>) => void;
  pushChatMessage: (message: ChatMessage) => void;
  replaceChatMessages: (messages: ChatMessage[]) => void;
  refreshChatStatus: () => Promise<void>;
  loadChatHistory: () => Promise<void>;
  setChatError: (message: string | null) => void;
  setChatSending: (sending: boolean) => void;
}

let toastSeq = 0;

export const useWebUi = create<WebUiState>((set, get) => ({
  projectRoot: "",
  status: null,
  statusLoading: false,
  toasts: [],
  lastError: null,
  connectionState: "connecting",
  chatMessages: [],
  chatStatus: null,
  chatLoading: false,
  chatError: null,
  chatSending: false,

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

  setConnectionState: (connectionState) => set({ connectionState }),

  setChatStatus: (chatStatus) => set({ chatStatus }),

  patchChatStatus: (patch) =>
    set((state) => ({
      chatStatus: state.chatStatus ? { ...state.chatStatus, ...patch } : state.chatStatus,
    })),

  pushChatMessage: (message) =>
    set((state) => {
      // Deduplicate by id so a reconnect replaying history doesn't double-append.
      if (state.chatMessages.some((entry) => entry.id === message.id)) {
        return state;
      }
      return { chatMessages: [...state.chatMessages, message] };
    }),

  replaceChatMessages: (chatMessages) => set({ chatMessages }),

  refreshChatStatus: async () => {
    set({ chatLoading: true });
    try {
      const chatStatus = await api.chatStatus();
      set({ chatStatus, chatLoading: false, chatError: null });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      set({ chatLoading: false, chatError: message });
    }
  },

  loadChatHistory: async () => {
    try {
      const messages = await api.chatHistory();
      set({ chatMessages: messages });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      set({ chatError: message });
    }
  },

  setChatError: (chatError) => set({ chatError }),

  setChatSending: (chatSending) => set({ chatSending }),
}));

// Map a live run-state hub event onto the snake_case ChatRunStatus shape.
function applyRunStateEvent(payload: RunStateEvent): Partial<ChatRunStatus> {
  const patch: Partial<ChatRunStatus> = {};
  if (payload.state !== undefined) patch.state = payload.state;
  if (payload.waitingFor !== undefined) patch.waiting_for = payload.waitingFor;
  if (payload.question !== undefined) patch.question = payload.question ?? null;
  if (payload.options !== undefined) patch.options = payload.options ?? null;
  if (payload.tool !== undefined || payload.reason !== undefined) {
    patch.permission = {
      tool: payload.tool,
      reason: payload.reason,
    };
  }
  if (payload.message !== undefined) patch.message = payload.message;
  return patch;
}

// Map a live progress-update hub event onto the snake_case ChatRunStatus shape.
function applyProgressEvent(payload: ProgressUpdateEvent): Partial<ChatRunStatus> {
  const patch: Partial<ChatRunStatus> = {};
  if (payload.round !== undefined) patch.round = payload.round;
  if (payload.newFindings !== undefined) patch.new_findings = payload.newFindings;
  if (payload.totalFindings !== undefined) patch.total_findings = payload.totalFindings;
  if (payload.costUsd !== undefined) patch.cost_usd = payload.costUsd;
  if (payload.converged !== undefined) patch.converged = payload.converged;
  if (payload.mode !== undefined) patch.mode = payload.mode;
  return patch;
}

// Hook to wire the SSE stream into the store. Called once from App.
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

  const parseJson = (event: Event): unknown => {
    try {
      return JSON.parse((event as MessageEvent).data) as unknown;
    } catch {
      return undefined;
    }
  };

  let source: EventSource | null = null;
  let closed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = (): void => {
    if (closed) return;
    useWebUi.getState().setConnectionState("connecting");
    const url = `/api/v1/events?stream=all${
      projectRoot ? `&project_root=${encodeURIComponent(projectRoot)}` : ""
    }`;
    source = new EventSource(url);
    source.addEventListener("status", (event) => {
      const payload = parseJson(event);
      if (payload !== undefined) onStatus(payload);
    });
    source.addEventListener("chat-message", (event) => {
      const payload = parseJson(event);
      if (payload !== undefined) {
        useWebUi.getState().pushChatMessage(payload as ChatMessage);
      }
    });
    source.addEventListener("run-state", (event) => {
      const payload = parseJson(event);
      if (payload !== undefined) {
        useWebUi
          .getState()
          .patchChatStatus(applyRunStateEvent(payload as RunStateEvent));
      }
    });
    source.addEventListener("progress-update", (event) => {
      const payload = parseJson(event);
      if (payload !== undefined) {
        useWebUi
          .getState()
          .patchChatStatus(applyProgressEvent(payload as ProgressUpdateEvent));
      }
    });
    source.onopen = (): void => {
      if (!closed) useWebUi.getState().setConnectionState("connected");
    };
    source.onerror = (): void => {
      // EventSource auto-reconnects; schedule a poll fallback as well.
      source?.close();
      if (closed) return;
      useWebUi.getState().setConnectionState("reconnecting");
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
  };

  connect();
  return () => {
    closed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    source?.close();
    useWebUi.getState().setConnectionState("disconnected");
  };
}
