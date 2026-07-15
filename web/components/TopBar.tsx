"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import FocusTimer from "@/components/FocusTimer";
import { type Mode, useMode } from "@/lib/mode";
import { useUi } from "@/lib/ui";

const TABS: { mode: Mode; label: string; short: string }[] = [
  { mode: "general", label: "GENERAL", short: "GE" },
  { mode: "study", label: "STUDY", short: "ST" },
  { mode: "research", label: "RESEARCH", short: "RE" },
  { mode: "code", label: "CODE", short: "CO" },
  { mode: "system", label: "SYSTEM", short: "SY" },
];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Live HH:MM:SS clock — one 1s interval, owned entirely by TopBar (§3, §10). */
function Clock() {
  const [time, setTime] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTime(`${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  // Render nothing until the first client tick — avoids an SSR/client mismatch
  // (the server has no "current" time to agree on).
  return <span className="tabular-nums">{time ?? "--:--:--"}</span>;
}

/** Sticky top bar (§3) — replaces Sidebar. Mode tabs + logo + utility cluster. */
export default function TopBar() {
  const { mode, setMode } = useMode();
  const { toggleDrawer, setNoteOpen, setPaletteOpen } = useUi();

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-void">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 md:px-8">
        <Link
          href="/dashboard"
          aria-label="Argus home"
          className="flex shrink-0 items-center gap-2"
        >
          <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full border border-[var(--ac)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--ac)]" />
          </span>
          <span className="font-mono text-sm font-semibold tracking-wide text-ink-bright">
            ARGUS<span className="text-[var(--ac)]">_</span>
          </span>
        </Link>

        <div
          role="tablist"
          aria-label="Mode"
          className="flex border border-line font-mono text-[11px] uppercase tracking-[0.14em]"
        >
          {TABS.map(({ mode: tabMode, label, short }) => {
            const active = mode === tabMode;
            return (
              <button
                key={tabMode}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setMode(tabMode)}
                className={`border-r border-line px-2.5 py-1.5 transition-colors last:border-r-0 md:px-3 ${
                  active
                    ? "bg-[var(--ac-bg)] text-[var(--ac)] shadow-[inset_0_-2px_0_var(--ac)]"
                    : "text-ink-faint hover:text-ink-muted"
                }`}
              >
                <span className="md:hidden">{short}</span>
                <span className="hidden md:inline">{label}</span>
              </button>
            );
          })}
        </div>

        <div className="ml-auto flex items-center gap-2 font-mono text-[11px] text-ink-faint">
          <div className="hidden items-center gap-2 md:flex">
            <button
              type="button"
              onClick={() => setNoteOpen(true)}
              className="border border-line px-2 py-1 uppercase tracking-[0.12em] transition-colors hover:border-lineHi hover:text-ink-muted"
            >
              + NOTE
            </button>
            <FocusTimer />
            <button
              type="button"
              aria-label="Chat"
              onClick={toggleDrawer}
              className="border border-line px-2 py-1 uppercase tracking-[0.12em] transition-colors hover:border-lineHi hover:text-ink-muted"
            >
              CHAT
            </button>
            <span className="flex items-center gap-1.5 border border-line px-2 py-1 uppercase tracking-[0.12em]">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-ok" />
              <span className="text-ok">LOCAL</span>
            </span>
          </div>
          <button
            type="button"
            aria-label="Command palette"
            onClick={() => setPaletteOpen(true)}
            className="border border-line px-2 py-1 transition-colors hover:border-lineHi hover:text-ink-muted"
          >
            [⌘K]
          </button>
          <span aria-label="Current time" className="border border-line px-2 py-1">
            <Clock />
          </span>
        </div>
      </div>
    </header>
  );
}
