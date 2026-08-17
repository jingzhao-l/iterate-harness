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
        {hasPending ? "!" : "💬"}
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