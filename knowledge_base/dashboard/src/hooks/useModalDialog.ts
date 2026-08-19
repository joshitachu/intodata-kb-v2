import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

interface Options {
  open: boolean;
  onClose: () => void;
  /** The dialog surface. Tab is trapped inside it. */
  containerRef: RefObject<HTMLElement | null>;
  /** Where focus lands on open. Falls back to the first focusable control. */
  initialFocusRef?: RefObject<HTMLElement | null>;
}

/**
 * The shared modal contract for every dialog in this dashboard: focus moves into
 * the dialog on open, Tab is trapped inside it, Escape closes it, and focus is
 * handed back to whatever had it before. Extracted so the Generate dialog and the
 * Documentation Agent dialog cannot drift apart on accessibility.
 */
export function useModalDialog({
  open,
  onClose,
  containerRef,
  initialFocusRef,
}: Options): void {
  // Remember what had focus, move focus into the dialog, hand it back on close.
  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement;
    const target =
      initialFocusRef?.current ??
      containerRef.current?.querySelector<HTMLElement>(FOCUSABLE) ??
      null;
    target?.focus();
    return () => {
      if (restoreTo instanceof HTMLElement) restoreTo.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const root = containerRef.current;
      if (!root) return;
      const focusable = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null || element === document.activeElement,
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (active instanceof HTMLElement && !root.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, onClose]);
}
