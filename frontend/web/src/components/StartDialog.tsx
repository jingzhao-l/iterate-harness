// StartDialog — modal to launch an iterate loop from the WebUI (design §18.3
// POST /chat/start). Lets the user pick the mode (review/run/resume), whether
// to limit to changed files, and the git ref for the changed-files diff.

import { useState } from "react";
import type { StartRequest } from "../types";
import { api } from "../api";
import { useWebUi } from "../store";

interface StartDialogProps {
  onClose: () => void;
  onStarted: () => void;
}

const MODES: Array<{
  value: StartRequest["mode"];
  label: string;
  description: string;
}> = [
  {
    value: "review",
    label: "评审（dry-run）",
    description: "只审查并产出 findings，不改动代码",
  },
  {
    value: "run",
    label: "修复（normal）",
    description: "审查 + 逐条修复，需要确认与权限",
  },
  {
    value: "resume",
    label: "续跑（resume）",
    description: "从上次的检查点继续",
  },
];

export function StartDialog({ onClose, onStarted }: StartDialogProps): React.JSX.Element {
  const [mode, setMode] = useState<StartRequest["mode"]>("review");
  const [changed, setChanged] = useState(false);
  const [ref, setRef] = useState("HEAD");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pushToast = useWebUi((state) => state.pushToast);
  const setChatStatus = useWebUi((state) => state.setChatStatus);
  const projectRoot = useWebUi((state) => state.projectRoot);

  const handleStart = async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.chatStart({ mode, changed, ref }, projectRoot);
      // Optimistically set an "starting" status so the panel reflects it immediately.
      setChatStatus({
        state: "starting",
        run_id: result.runId,
        mode,
        project_root: projectRoot,
        round: 0,
        new_findings: 0,
        total_findings: 0,
        cost_usd: 0,
        converged: false,
        waiting_for: "none",
        question: null,
        options: null,
        permission: null,
        error: null,
        message: "正在启动…",
      });
      onStarted();
    } catch (startError) {
      const message = startError instanceof Error ? startError.message : String(startError);
      setError(message);
      pushToast("error", `启动失败：${message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h3>启动迭代</h3>
        <p className="muted" style={{ margin: "0 0 14px" }}>
          在服务器进程内启动一次 iterate 循环，进度与决策会实时推送到对话面板。
        </p>

        <div className="mode-options">
          {MODES.map((option) => (
            <button
              key={option.value}
              className={`mode-option ${mode === option.value ? "active" : ""}`}
              onClick={() => setMode(option.value)}
              disabled={busy}
            >
              <span className="mode-label">{option.label}</span>
              <span className="mode-desc">{option.description}</span>
            </button>
          ))}
        </div>

        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={changed}
            onChange={(event) => setChanged(event.target.checked)}
            disabled={busy}
          />
          仅审查自 ref 以来的变更文件
        </label>

        {changed && (
          <label className="field-label">
            Git ref（变更基准）
            <input
              type="text"
              value={ref}
              onChange={(event) => setRef(event.target.value)}
              disabled={busy}
              placeholder="HEAD"
              style={{ marginTop: 6 }}
            />
          </label>
        )}

        {error && <p className="form-error">{error}</p>}

        <div className="actions" style={{ marginTop: 16 }}>
          <button className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="btn primary" onClick={() => void handleStart()} disabled={busy}>
            {busy ? "启动中…" : "启动"}
          </button>
        </div>
      </div>
    </div>
  );
}
