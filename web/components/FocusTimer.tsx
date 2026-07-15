"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import { useUi } from "@/lib/ui";

const DEFAULT_SECONDS = 25 * 60;

function format(seconds: number): string {
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

/**
 * Focus timer [PREVIEW] — the TopBar ◔ FOCUS chip. Local state only; the ONE
 * timer interval exists only while running (§10 — TopBar's clock is the only
 * other interval). While running/paused the chip shows the mm:ss countdown.
 * The palette starts sessions via the UiContext registration hook.
 */
export default function FocusTimer() {
  const { registerFocusStart } = useUi();
  const { show } = useToast();
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [remaining, setRemaining] = useState(DEFAULT_SECONDS);
  const rootRef = useRef<HTMLDivElement>(null);

  // interval only while running
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setRemaining((prev) => Math.max(prev - 1, 0)), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  // completion: toast + reset
  useEffect(() => {
    if (remaining !== 0) return;
    setRunning(false);
    setRemaining(DEFAULT_SECONDS);
    show("focus :: session complete");
  }, [remaining, show]);

  // palette hook: `start focus session` starts (and reveals) the countdown
  const start = useCallback(() => {
    setRunning(true);
    setOpen(true);
  }, []);
  useEffect(() => {
    registerFocusStart(start);
    return () => registerFocusStart(null);
  }, [registerFocusStart, start]);

  // popover dismissal: Escape or click outside
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  const engaged = running || remaining !== DEFAULT_SECONDS;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="Focus timer"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`border border-line px-2 py-1 uppercase tracking-[0.12em] transition-colors hover:border-lineHi ${
          engaged ? "text-[var(--ac)]" : "hover:text-ink-muted"
        }`}
      >
        ◔ {engaged ? format(remaining) : "FOCUS"}
      </button>

      {open && (
        <div className="animate-palette absolute right-0 top-full z-40 mt-2 w-52 border border-line bg-panel p-4">
          <div className="mb-3 flex items-center gap-2">
            <p className="eyebrow">▍FOCUS</p>
            <span className="border border-[#3d2f66] px-1 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">
              PREVIEW
            </span>
          </div>
          <p className="mb-3 text-center font-mono text-2xl font-semibold tabular-nums text-ink-bright">
            {format(remaining)}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setRunning((value) => !value)}
              className="flex-1 border border-line bg-[var(--ac-bg)] px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--ac)] transition-colors hover:border-lineHi"
            >
              {running ? "PAUSE" : "START"}
            </button>
            <button
              type="button"
              onClick={() => {
                setRunning(false);
                setRemaining(DEFAULT_SECONDS);
              }}
              className="flex-1 border border-line px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-faint transition-colors hover:border-lineHi hover:text-ink-muted"
            >
              RESET
            </button>
          </div>
          <p className="mt-3 font-mono text-[9.5px] leading-relaxed text-ink-faint">
            local only — sessions aren&apos;t logged yet
          </p>
        </div>
      )}
    </div>
  );
}
