// StartDialog — modal to launch an iterate loop from the WebUI (design §18.3
// POST /chat/start). Lets the user pick the mode (review/run/resume), whether
// to limit to changed files, and the git ref for the changed-files diff.
// Application modal: Escape closes, focus is trapped + restored, Enter submits
// (wrapped in a <form>), and the mode options carry aria-pressed state.

import { useMemo, useState } from "react";
import type { StartRequest } from "../types";
import { api } from "../api";
import { useWebUi } from "../store";
import { useDialog } from "./useDialog";

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

// Git refs must be non-empty and must not contain whitespace or the special
// characters git itself forbids (~ ^ : ? * [ \ and a trailing dot-slash
// tricks). We validate on the client so a typo surfaces before the POST.
const REF_INVALID_RE = /[\s~^:?*[\]\\]/;

export function StartDialog({ onClose, onStarted }: StartDialogProps): React.JSX.Element {
  const [mode, setMode] = useState<StartRequest["mode"]>("review");
  const [changed, setChanged] = useState(false);
  const [ref, setRef] = useState("HEAD");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pushToast = useWebUi((state) => state.pushToast);
  const setChatStatus = useWebUi((state) => state.setChatStatus);
  const projectRoot = useWebUi((state) => state.projectRoot);

  // While a request is in flight the dialog stays open and is not dismissible.
  const { containerRef, titleId } = useDialog<HTMLFormElement>({
    open: true,
    onClose: busy ? undefined : onClose,
  });

  const refError = useMemo(() => {
    if (mode === "resume" || !changed) return null;
    if (!ref.trim()) return "请输入 git ref（如 HEAD 或 main）";
    if (REF_INVALID_RE.test(ref)) return "git ref 不能包含空格或特殊字符（~ ^ : ? * [ \\）";
    return null;
  }, [mode, changed, ref]);

  // "changed"/"ref" only apply to review/run (the diff baseline for a
  // changed-only pass). "resume" continues from the last checkpoint, so those
  // options are meaningless there: switching into resume hides them and
  // clears the flag so a stale "changed" never leaks into the kickoff.
  const selectMode = (next: StartRequest["mode"]): void => {
    setMode(next);
    if (next === "resume") setChanged(false);
  };

  const handleStart = async (): Promise<void> => {
    if (busy || refError) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.chatStart({ mode, changed, ref: ref.trim() }, projectRoot);
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
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={busy}
        ref={containerRef}
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          void handleStart();
        }}
      >
        <h3 id={titleId}>启动迭代</h3>
        <p className="muted" style={{ margin: "0 0 14px" }}>
          在服务器进程内启动一次 iterate 循环，进度与决策会实时推送到对话面板。
        </p>

        <div className="mode-options" role="group" aria-label="运行模式">
          {MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`mode-option ${mode === option.value ? "active" : ""}`}
              aria-pressed={mode === option.value}
              onClick={() => selectMode(option.value)}
              disabled={busy}
            >
              <span className="mode-label">{option.label}</span>
              <span className="mode-desc">{option.description}</span>
            </button>
          ))}
        </div>

        {mode !== "resume" && (
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={changed}
              onChange={(event) => setChanged(event.target.checked)}
              disabled={busy}
            />
            仅审查自 ref 以来的变更文件
          </label>
        )}

        {mode !== "resume" && changed && (
          <label className="field-label">
            Git ref（变更基准）
            <input
              type="text"
              value={ref}
              onChange={(event) => setRef(event.target.value)}
              disabled={busy}
              placeholder="HEAD"
              style={{ marginTop: 6 }}
              aria-invalid={refError !== null}
            />
            {refError && <span className="field-error">{refError}</span>}
          </label>
        )}

        {error && <p className="form-error">{error}</p>}

        <div className="actions" style={{ marginTop: 16 }}>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="submit" className="btn primary" disabled={busy || refError !== null}>
            {busy ? "启动中…" : "启动"}
          </button>
        </div>
      </form>
    </div>
  );
}
