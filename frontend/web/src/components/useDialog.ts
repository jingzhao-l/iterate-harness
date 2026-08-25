// useDialog — shared modal behavior for every dialog in the WebUI.
//
// A dialog is an application modal: it captures focus (Tab never escapes the
// box), closes on Escape, restores focus to the previously focused element on
// close, and announces itself to assistive technology via role="dialog" +
// aria-modal + a stable labelled-by id. It also bumps the global modalCount
// (store) so the App-level global shortcuts ("g <key>", "/") are suppressed
// while the dialog is open — typing inside a dialog must never navigate the
// page behind it.
//
// Callers own the backdrop/markup; this hook only wires behaviour + semantics.
// The generic parameter is the dialog's root element type (HTMLDivElement for
// most dialogs, HTMLFormElement when the dialog is a <form>).

import { useEffect, useId, useRef } from "react";
import { useWebUi } from "../store";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface UseDialogOptions {
  /** Whether the dialog is currently mounted/open. */
  open: boolean;
  /** Close handler invoked on Escape / backdrop click. Pass undefined to
   *  disable keyboard + backdrop dismissal (e.g. while a request is busy). */
  onClose: (() => void) | undefined;
}

export interface DialogBehavior<T extends HTMLElement> {
  /** Attach to the dialog's content element (the one that owns the focus
   *  trap and carries role="dialog"). */
  containerRef: React.RefObject<T>;
  /** Stable id to bind aria-labelledby (bind to the dialog's title). */
  titleId: string;
}

export function useDialog<T extends HTMLElement = HTMLDivElement>({
  open,
  onClose,
}: UseDialogOptions): DialogBehavior<T> {
  const containerRef = useRef<T>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    useWebUi.getState().setModalOpen(true);

    const container = containerRef.current;
    // Move focus into the dialog: first focusable element wins, otherwise the
    // dialog container itself (which is focusable via tabindex="-1").
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const firstFocusable = container?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    if (firstFocusable) {
      firstFocusable.focus();
    } else if (container) {
      container.setAttribute("tabindex", "-1");
      container.focus();
    }

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        // Escape always dismisses unless the caller disabled closing.
        if (onClose) {
          event.stopPropagation();
          event.preventDefault();
          onClose();
        }
        return;
      }
      // Trap Tab inside the dialog.
      if (event.key !== "Tab" || !container) return;
      const focusables = Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === container)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || active === container)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      useWebUi.getState().setModalOpen(false);
      // Restore focus to wherever the user was before the dialog opened.
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  return { containerRef, titleId };
}
