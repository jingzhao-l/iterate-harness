// Store unit tests — live hub event mapping (snake_case patch), chat-message
// dedup and the toast lifecycle (design §18 / §17.9 quality gates). The API
// layer is mocked because api.ts touches window/sessionStorage at import time.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyProgressEvent, applyRunStateEvent, useWebUi } from "../store";

vi.mock("../api", () => ({
  api: {
    status: vi.fn(),
    timeline: vi.fn(),
    findings: vi.fn(),
    triageDecisions: vi.fn(),
    triageFinding: vi.fn(),
    clearTriage: vi.fn(),
    chatStatus: vi.fn(),
    chatHistory: vi.fn(),
    chatStart: vi.fn(),
    chatSend: vi.fn(),
    chatControl: vi.fn(),
  },
  webuiToken: () => "",
}));

describe("applyRunStateEvent", () => {
  it("maps state and waitingFor onto snake_case fields", () => {
    expect(applyRunStateEvent({ state: "running", waitingFor: "permission" })).toEqual({
      state: "running",
      waiting_for: "permission",
    });
  });

  it("maps question/options and nulls them explicitly", () => {
    const patch = applyRunStateEvent({
      question: "继续？",
      options: [{ value: "y", label: "是" }],
    });
    expect(patch.question).toBe("继续？");
    expect(patch.options).toEqual([{ value: "y", label: "是" }]);

    expect(applyRunStateEvent({ question: null, options: null })).toEqual({
      question: null,
      options: null,
    });
  });

  it("combines tool/reason into a permission object", () => {
    expect(applyRunStateEvent({ tool: "bash", reason: "exec" })).toEqual({
      permission: { tool: "bash", reason: "exec" },
    });
    expect(applyRunStateEvent({ tool: "bash" })).toEqual({
      permission: { tool: "bash", reason: undefined },
    });
  });

  it("maps message and ignores an empty event", () => {
    expect(applyRunStateEvent({ message: "hi" })).toEqual({ message: "hi" });
    expect(applyRunStateEvent({})).toEqual({});
  });
});

describe("applyProgressEvent", () => {
  it("maps camelCase progress fields to snake_case", () => {
    expect(
      applyProgressEvent({
        round: 3,
        newFindings: 2,
        totalFindings: 10,
        costUsd: 1.25,
        converged: true,
        mode: "run",
      }),
    ).toEqual({
      round: 3,
      new_findings: 2,
      total_findings: 10,
      cost_usd: 1.25,
      converged: true,
      mode: "run",
    });
  });

  it("maps partial updates and ignores an empty event", () => {
    expect(applyProgressEvent({ converged: false })).toEqual({ converged: false });
    expect(applyProgressEvent({})).toEqual({});
  });
});

describe("store chat state", () => {
  beforeEach(() => {
    useWebUi.setState({ chatMessages: [], toasts: [], chatStatus: null });
  });

  it("deduplicates chat messages by id", () => {
    const message = {
      id: "m1",
      role: "user" as const,
      kind: "text",
      content: "hello",
      timestamp: "2026-01-01T00:00:00Z",
    };
    useWebUi.getState().pushChatMessage(message);
    useWebUi.getState().pushChatMessage(message);
    useWebUi.getState().pushChatMessage({ ...message, id: "m2" });
    expect(useWebUi.getState().chatMessages).toHaveLength(2);
    expect(useWebUi.getState().chatMessages.map((entry) => entry.id)).toEqual([
      "m1",
      "m2",
    ]);
  });
});

describe("toast lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useWebUi.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("appends a toast and auto-dismisses it after 4s", () => {
    useWebUi.getState().pushToast("info", "已保存");
    expect(useWebUi.getState().toasts).toHaveLength(1);
    expect(useWebUi.getState().toasts[0]).toMatchObject({
      kind: "info",
      message: "已保存",
    });

    vi.advanceTimersByTime(4000);
    expect(useWebUi.getState().toasts).toHaveLength(0);
  });

  it("dismisses only the targeted toast", () => {
    useWebUi.getState().pushToast("success", "a");
    useWebUi.getState().pushToast("error", "b");
    const firstId = useWebUi.getState().toasts[0].id;
    useWebUi.getState().dismissToast(firstId);
    expect(useWebUi.getState().toasts.map((t) => t.message)).toEqual(["b"]);
  });
});
