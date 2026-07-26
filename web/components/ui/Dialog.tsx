"use client";

import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";

/**
 * The one overlay implementation. Before this, six surfaces each re-implemented
 * backdrop + Escape + dismissal by hand, three of them repeating the identical
 * `fixed inset-0 z-50 bg-[rgba(3,2,8,0.72)]` literal, and only NoteModal ever
 * got the focus trap right — this is that trap, lifted and shared.
 *
 * What every dialog gets for free: a focus trap, initial focus inside the
 * dialog, Escape, focus restored to whatever opened it, and a body scroll lock
 * (which nothing had).
 */

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * Dialogs nest — the engine picker opens the add-model dialog inside itself —
 * so "am I the one that should react to Escape?" has to be a shared fact
 * rather than a per-instance guess. Only the top of the stack handles keys.
 */
const stack: string[] = [];

/**
 * The scroll lock is refcounted for the same reason: an inner dialog closing
 * must not hand scrolling back to the page while the outer one is still open.
 */
let lockCount = 0;
let restoreOverflow = "";

export default function Dialog({
  label,
  onClose,
  className = "",
  align = "top",
  initialFocusRef,
  children,
}: {
  /** Accessible name. Playwright addresses dialogs by this, so treat it as API. */
  label: string;
  onClose: () => void;
  /** Width and padding — everything else is owned here. */
  className?: string;
  /** `top` sits it under the title bar; `center` suits taller content. */
  align?: "top" | "center";
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const id = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // Held in a ref so an inline `onClose={() => ...}` cannot re-run the mount
  // effect on every render — that would re-push onto the stack and yank focus
  // back to the top of the dialog mid-typing.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    stack.push(id);

    if (lockCount === 0) {
      restoreOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    lockCount += 1;

    // Caller's choice, else the first control, else the dialog itself — focus
    // must never be left behind on a trigger that is now under the backdrop.
    const target =
      initialFocusRef?.current ??
      dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE) ??
      dialogRef.current;
    target?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (stack[stack.length - 1] !== id) return;

      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }

      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusables = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusables.length === 0) return;

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    const restore = restoreRef.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      const index = stack.indexOf(id);
      if (index !== -1) stack.splice(index, 1);
      lockCount -= 1;
      if (lockCount === 0) document.body.style.overflow = restoreOverflow;
      restore?.focus?.();
    };
    // `initialFocusRef` is a ref object and stable; `onClose` is read through
    // onCloseRef. Mount-once is the intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-[rgba(3,2,8,0.72)]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCloseRef.current();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={`animate-palette mx-auto mb-[8vh] border border-lineHi bg-panel outline-none ${
          align === "center" ? "mt-[8vh]" : "mt-[12vh]"
        } ${className}`}
      >
        {children}
      </div>
    </div>
  );
}
