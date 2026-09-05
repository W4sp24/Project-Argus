"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastTone = "ok" | "error";

interface ToastState {
  /**
   * Show a terminal-line toast. Up to three stack; older ones drop off.
   * `tone: "error"` colors it, announces it assertively, and holds it longer.
   */
  show: (message: string, options?: { tone?: ToastTone }) => void;
}

const ToastContext = createContext<ToastState | null>(null);

export function useToast(): ToastState {
  const state = useContext(ToastContext);
  if (!state) throw new Error("useToast must be used inside <ToastProvider>");
  return state;
}

/** Failures get read time; confirmations do not need it. */
const DISMISS_MS: Record<ToastTone, number> = { ok: 3200, error: 6000 };
const MAX_VISIBLE = 3;

interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

/**
 * Fixed bottom-left terminal lines `> message` (§5).
 *
 * Two things changed from the original single-line version, both because
 * failures were getting lost: toasts stack instead of replacing each other (a
 * save failure could be erased by an unrelated success before it was read),
 * and errors are visually and assistively distinct from confirmations (both
 * used to render identically and announce politely).
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((id) => window.clearTimeout(id));
  }, []);

  const show = useCallback((message: string, options?: { tone?: ToastTone }) => {
    const tone = options?.tone ?? "ok";
    const id = (nextId.current += 1);
    setToasts((prev) => [...prev, { id, message, tone }].slice(-MAX_VISIBLE));
    const timer = window.setTimeout(() => {
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, DISMISS_MS[tone]);
    timers.current.push(timer);
  }, []);

  // `show` is already stable, but `{ show }` was not: a new object every time
  // the provider re-rendered, which is every toast added and every one
  // auto-dismissed. `useToast` has the widest consumer list in the app, so
  // that re-rendered TopBar, ChatDrawer, the command palette, the planner
  // timeline, the ingest panel and the thread rail on every toast. Memoised,
  // this value now never changes at all.
  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}

      {/* One always-mounted live region holding the visible stack. Mirroring
          the text into a second sr-only region would announce more reliably,
          but it puts every message in the DOM twice — which is a real cost to
          anything reading the page, tests included. Errors carry role="alert"
          instead, which screen readers treat as assertive. */}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 left-4 z-50 flex flex-col gap-2"
      >
        {toasts.map((toast) => (
          <p
            key={toast.id}
            role={toast.tone === "error" ? "alert" : "status"}
            className={`animate-toast max-w-[26rem] border bg-panel px-3 py-2 font-mono text-label ${
              toast.tone === "error" ? "border-danger text-danger" : "border-line text-ink"
            }`}
          >
            {`> ${toast.message}`}
          </p>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
