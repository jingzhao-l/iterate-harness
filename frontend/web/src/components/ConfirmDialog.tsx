// ConfirmDialog — secondary confirmation for every mutating operation
// (design §17.3 "显式确认"; the backend also requires confirm=true).
// Application modal: Escape closes, focus is trapped + restored, and the box
// announces itself via role="dialog"/aria-modal (see useDialog.ts).

import type { ReactNode } from "react";
import { useDialog } from "./useDialog";

interface ConfirmDialogProps {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): React.JSX.Element {
  // While a request is in flight the dialog stays open and is not dismissible.
  const { containerRef, titleId } = useDialog({
    open: true,
    onClose: busy ? undefined : onCancel,
  });

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={busy}
        ref={containerRef}
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id={titleId}>{title}</h3>
        <p>{children}</p>
        <div className="actions">
          <button className="btn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            className={`btn ${danger ? "danger" : "primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
