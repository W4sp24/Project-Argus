"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

interface ToastState {
  /** Show a single terminal-line toast. Replaces any visible toast (never stacks). */
  show: (message: string) => void;
}

const ToastContext = createContext<ToastState | null>(null);

export function useToast(): ToastState {
  const state = useContext(ToastContext);
  if (!state) throw new Error("useToast must be used inside <ToastProvider>");
  return state;
}

const DISMISS_MS = 3200;

/**
 * Single fixed bottom-left terminal line `> message` (§5).
 * One at a time — a new message replaces the current one and restarts the
 * dismiss timer. The live region is always mounted so screen readers announce
 * message swaps politely.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ message: string; id: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const show = useCallback((message: string) => {
    window.clearTimeout(timer.current);
    setToast((prev) => ({ message, id: (prev?.id ?? 0) + 1 }));
    timer.current = window.setTimeout(() => setToast(null), DISMISS_MS);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-4 z-50">
        {toast && (
          <p
            key={toast.id}
            className="animate-toast border border-line bg-panel px-3 py-2 font-mono text-xs text-ink"
          >
            {`> ${toast.message}`}
          </p>
        )}
      </div>
    </ToastContext.Provider>
  );
}
