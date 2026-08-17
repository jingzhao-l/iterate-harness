// ChatPanel — the human-in-the-loop conversation surface (design §18.4).
// A collapsible sidebar panel on the right side of the main layout. The
// harness center is the run, not the conversation, so the panel is compact
// and only expands when the user opens it or when a decision is pending.

import { useEffect, useRef, useState } from "react";
import RunStatusCard from "./RunStatusCard";
import { StartDialog } from "./StartDialog";
import type { ChatMessage } from "../types";
import { api } from "../api";
import { useWebUi } from "../store";

// Allowed to send: running (nudge) or paused waiting for input.
function canSend(state: string | undefined, waitingFor: string | undefined): boolean {
  return state === "running" || (state === "paused" && waitingFor !== "none");
}

export default function ChatPanel(): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [showStart, setShowStart] = useState(false);
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const chatMessages = useWebUi((state) => state.chatMessages);
  const chatStatus = useWebUi((state) => state.chatStatus);
  const chatLoading = useWebUi((state) => state.chatLoading);
  const chatSending = useWebUi((state) => state.chatSending);
  const pushToast = useWebUi((state) => state.pushToast);
  const setChatError = useWebUi((state) => state.setChatError);
  const setChatSending = useWebUi((state) => state.setChatSending);
  const refreshChatStatus = useWebUi((state) => state.refreshChatStatus);
  const loadChatHistory = useWebUi((state) => state.loadChatHistory);

  // Auto-open when a decision is pending.
  useEffect(() => {
    if (
      chatStatus &&
      (chatStatus.waiting_for === "permission" ||
        chatStatus.waiting_for === "user_select" ||
        chatStatus.waiting_for === "user_prompt")
    ) {
      setOpen(true);
    }
  }, [chatStatus?.waiting_for]);

  // Load history once on mount.
  useEffect(() => {
    void loadChatHistory();
    void refreshChatStatus();
  }, []);

  // Global shortcut ("/" or Cmd/Ctrl+K) dispatches an event to toggle the panel.
  useEffect(() => {
    const onToggle = (): void => setOpen((prev) => !prev);
    window.addEventListener("iterate:toggle-chat", onToggle);
    return () => window.removeEventListener("iterate:toggle-chat", onToggle);
  }, []);

  // Auto-scroll to bottom on new messages.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const handleSend = async (): Promise<void> => {
    const text = input.trim();
    if (!text || chatSending) return;
    setInput("");
    setChatSending(true);
    try {
      await api.chatSend(text);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setChatError(message);
      pushToast("error", `发送失败：${message}`);
    } finally {
      setChatSending(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent): void => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  };

  const handleControl = async (action: "pause" | "resume" | "stop"): Promise<void> => {
    try {
      const result = await api.chatControl(action);
      if (result.message) pushToast("info", result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      pushToast("error", message);
    }
  };

  const handleStart = (): void => setShowStart(true);

  const hasPending =
    chatStatus &&
    (chatStatus.waiting_for === "permission" ||
      chatStatus.waiting_for === "user_select" ||
      chatStatus.waiting_for === "user_prompt");

  const decisionOption = chatStatus?.options ?? null;

  return (
    <>
      {/* Toggle button — always visible bottom-right */}
      <button
        className={`chat-toggle ${hasPending ? "has-pending" : ""}`}
        onClick={() => setOpen((prev) => !prev)}
        title={open ? "收起对话面板" : "打开对话面板"}
      >
        {hasPending ? (
          <span className="chat-toggle-badge">!</span>
        ) : (
          <svg
            className="chat-toggle-icon"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
        )}
      </button>

      {/* Slide-out panel */}
      {open && (
        <aside className="chat-panel">
          <div className="chat-header">
            <span className="chat-title">对话控制</span>
            <button className="btn" onClick={() => setOpen(false)}>
              关闭
            </button>
          </div>

          <div className="chat-body">
            <RunStatusCard
              status={chatStatus}
              loading={chatLoading}
              busy={chatSending}
              onControl={handleControl}
              onStart={handleStart}
            />

            {/* Decision actions: quick-action buttons for select/permission */}
            {decisionOption && decisionOption.length > 0 && (
              <div className="decision-actions">
                {decisionOption.map((opt) => (
                  <button
                    key={opt.value}
                    className="btn decision-btn"
                    disabled={chatSending}
                    onClick={() => {
                      setChatSending(true);
                      api
                        .chatSend(opt.value)
                        .catch((error) => {
                          pushToast(
                            "error",
                            `选择失败：${error instanceof Error ? error.message : String(error)}`,
                          );
                        })
                        .finally(() => setChatSending(false));
                    }}
                  >
                    {opt.label}
                    {opt.description && (
                      <span className="decision-desc">{opt.description}</span>
                    )}
                  </button>
                ))}
              </div>
            )}

            {/* Permission quick buttons */}
            {chatStatus?.waiting_for === "permission" && (
              <div className="decision-actions">
                <button
                  className="btn primary"
                  disabled={chatSending}
                  onClick={() => {
                    setChatSending(true);
                    api
                      .chatSend("yes")
                      .catch((error) => {
                        pushToast(
                          "error",
                          `发送失败：${error instanceof Error ? error.message : String(error)}`,
                        );
                      })
                      .finally(() => setChatSending(false));
                  }}
                >
                  批准
                </button>
                <button
                  className="btn danger"
                  disabled={chatSending}
                  onClick={() => {
                    setChatSending(true);
                    api
                      .chatSend("no")
                      .catch((error) => {
                        pushToast(
                          "error",
                          `发送失败：${error instanceof Error ? error.message : String(error)}`,
                        );
                      })
                      .finally(() => setChatSending(false));
                  }}
                >
                  拒绝
                </button>
              </div>
            )}

            {/* Message list */}
            <div className="chat-messages">
              {chatMessages.length === 0 && (
                <p className="muted" style={{ textAlign: "center", padding: 24 }}>
                  暂无对话记录。启动迭代后，系统消息和决策请求将显示在此处。
                </p>
              )}
              {chatMessages.map((msg) => (
                <ChatBubble key={msg.id} message={msg} />
              ))}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input bar */}
          <div className="chat-input-bar">
            <textarea
              className="chat-input"
              placeholder={
                chatStatus?.waiting_for !== "none"
                  ? "输入回答…"
                  : chatStatus?.state === "running"
                    ? "输入督促消息（将在下一轮边界注入）…"
                    : "输入消息…"
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={chatSending || !canSend(chatStatus?.state, chatStatus?.waiting_for)}
              rows={2}
            />
            <button
              className="btn primary"
              onClick={() => void handleSend()}
              disabled={chatSending || !input.trim() || !canSend(chatStatus?.state, chatStatus?.waiting_for)}
            >
              {chatSending ? "发送中…" : "发送"}
            </button>
          </div>
        </aside>
      )}

      {showStart && (
        <StartDialog
          onClose={() => setShowStart(false)}
          onStarted={() => {
            setShowStart(false);
            setOpen(true);
            pushToast("success", "迭代已启动");
          }}
        />
      )}
    </>
  );
}

// ChatBubble — one message rendered in the chat stream.
function ChatBubble({ message }: { message: ChatMessage }): React.JSX.Element {
  // Tool messages (kind === "tool") render as compact activity cards so the
  // live tool-call stream reads as a timeline instead of raw text (design §18).
  if (message.kind === "tool") {
    return <ToolCallCard content={message.content} timestamp={message.timestamp} />;
  }

  const roleClass = message.role === "user" ? "bubble-user" : message.role === "assistant" ? "bubble-assistant" : "bubble-system";
  const kindTag = message.kind !== "text" ? message.kind : "";

  return (
    <div className={`chat-bubble ${roleClass}`}>
      {kindTag && <span className="bubble-kind">{kindTag}</span>}
      <div className="bubble-content">{message.content}</div>
      <span className="bubble-time">
        {message.timestamp.slice(11, 19)}
      </span>
    </div>
  );
}

// Parse a tool line like "▶ 调用工具 iterate_review" or "✔ iterate_review：preview"
// into (status, toolName, detail). Unknown shapes degrade to a generic card.
interface ParsedToolLine {
  status: "start" | "done" | "idle";
  toolName: string;
  detail: string;
}

function parseToolLine(content: string): ParsedToolLine {
  const text = content.trim();
  if (text.startsWith("▶")) {
    const rest = text.replace(/^▶\s*/, "").replace(/^调用工具\s*/, "");
    return { status: "start", toolName: rest, detail: "" };
  }
  const match = text.match(/^[✔✓✖✗]\s*([^：:]+)[：:]\s*(.*)$/s);
  if (match) {
    const mark = text[0];
    return {
      status: mark === "✔" || mark === "✓" ? "done" : "idle",
      toolName: match[1].trim(),
      detail: match[2].trim(),
    };
  }
  return { status: "idle", toolName: text, detail: "" };
}

function ToolCallCard({ content, timestamp }: { content: string; timestamp: string }): React.JSX.Element {
  const parsed = parseToolLine(content);
  const statusLabel =
    parsed.status === "start" ? "执行中" : parsed.status === "done" ? "完成" : "工具";
  const statusClass =
    parsed.status === "start" ? "tool-start" : parsed.status === "done" ? "tool-done" : "tool-idle";

  return (
    <div className={`tool-card ${statusClass}`}>
      <div className="tool-card-head">
        <span className="tool-card-dot" aria-hidden="true" />
        <span className="tool-card-name mono">{parsed.toolName}</span>
        <span className="tool-card-status">{statusLabel}</span>
        <span className="tool-card-time">{timestamp.slice(11, 19)}</span>
      </div>
      {parsed.detail && <div className="tool-card-detail">{parsed.detail}</div>}
    </div>
  );
}