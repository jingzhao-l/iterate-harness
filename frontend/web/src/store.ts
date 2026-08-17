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
  // Monotonic revision bumped whenever the SSE stream reports new decision-log
  // entries. Pages that render decision-log data (Runs: timeline + findings)
  // watch this to refetch while a loop is running live.
  logRevision: number;
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
  bumpLogRevision: () => void;
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
  logRevision: 0,
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

  bumpLogRevision: () => set((state) => ({ logRevision: state.logRevision + 1 })),

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
  // Track the last waiting state so we can fire a browser notification once
  // per transition into a human-in-the-loop request (design §18.4).
  let lastWaitingFor: string | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let notificationGranted = false;

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

  // Fallback polling keeps the dashboard + chat state fresh whenever the SSE
  // stream is down (design §17 UX resilience). Started on error, stopped on
  // reconnect success, so we never double-poll while the stream is healthy.
  const startPolling = (): void => {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      void useWebUi.getState().refreshStatus();
      void useWebUi.getState().refreshChatStatus();
    }, 5000);
  };

  const stopPolling = (): void => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  // Fire a Notification when the harness needs human input while the tab is
  // hidden (or when the user has granted permission explicitly).
  const maybeNotifyWaiting = (waitingFor: string): void => {
    const isHumanRequest =
      waitingFor === "permission" ||
      waitingFor === "user_select" ||
      waitingFor === "user_prompt";
    if (!isHumanRequest || waitingFor === lastWaitingFor) return;
    lastWaitingFor = waitingFor;

    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
      notificationGranted = true;
    } else if (Notification.permission === "default") {
      void Notification.requestPermission().then((permission) => {
        if (permission === "granted") notificationGranted = true;
      });
    }
    if (!notificationGranted && Notification.permission !== "granted") return;

    const labels: Record<string, string> = {
      permission: "iterate 需要你的授权",
      user_select: "iterate 需要你的选择",
      user_prompt: "iterate 需要补充信息",
    };
    const status = useWebUi.getState().chatStatus;
    const question = status?.question;
    try {
      const notification = new Notification(labels[waitingFor] ?? "iterate 需要输入", {
        body: question || "请打开对话面板处理迭代请求。",
        tag: "iterate-waiting",
      });
      notification.onclick = (): void => {
        window.focus();
        window.dispatchEvent(new CustomEvent("iterate:toggle-chat"));
        notification.close();
      };
    } catch {
      // Notification constructor can throw on some platforms; degrade silently.
    }
  };

  let source: EventSource | null = null;
  let closed = false;
  let everConnected = false;
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
    // New decision-log entries were appended since the last poll: bump the
    // revision so decision-log pages (Runs) refetch their live data.
    source.addEventListener("decision-log", (event) => {
      if (parseJson(event) !== undefined) {
        useWebUi.getState().bumpLogRevision();
      }
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
        const runEvent = payload as RunStateEvent;
        useWebUi.getState().patchChatStatus(applyRunStateEvent(runEvent));
        if (runEvent.waitingFor !== undefined) {
          maybeNotifyWaiting(runEvent.waitingFor);
        }
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
      if (closed) return;
      // Reconnect succeeded: stop fallback polling, reset notification memory.
      stopPolling();
      lastWaitingFor = null;
      useWebUi.getState().setConnectionState("connected");
      // Only show a toast on reconnection, not on the initial connect.
      if (everConnected) {
        useWebUi.getState().pushToast("success", "实时流已重新连接");
      }
      everConnected = true;
    };
    source.onerror = (): void => {
      // EventSource auto-reconnects; schedule a poll fallback as well.
      source?.close();
      if (closed) return;
      useWebUi.getState().setConnectionState("reconnecting");
      startPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
  };

  connect();
  return () => {
    closed = true;
    stopPolling();
    if (reconnectTimer) clearTimeout(reconnectTimer);
    source?.close();
    useWebUi.getState().setConnectionState("disconnected");
  };
}
