"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { EngineTrigger } from "@/components/EnginePicker";
import FocusTimer from "@/components/FocusTimer";
import Button from "@/components/ui/Button";
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

/**
 * Below `md` the utility cluster used to be `hidden`, which removed + NOTE,
 * the focus timer and CHAT from the app entirely rather than relocating them.
 * This is where they go instead.
 */
function OverflowMenu() {
  const { setNoteOpen, toggleDrawer, startFocus } = useUi();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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

  const items: { label: string; run: () => void }[] = [
    { label: "+ NOTE", run: () => setNoteOpen(true) },
    { label: "CHAT", run: toggleDrawer },
    { label: "◔ FOCUS", run: startFocus },
  ];

  return (
    <div ref={rootRef} className="relative md:hidden">
      <Button
        variant="quiet"
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        ⋯
      </Button>
      {open && (
        <div
          role="menu"
          aria-label="More actions"
          className="animate-palette absolute right-0 top-full z-40 mt-1 w-44 border border-line bg-panel"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                item.run();
                setOpen(false);
              }}
              className="block w-full border-b border-line px-3 py-2.5 text-left font-mono text-label uppercase tracking-[0.12em] text-ink-muted transition-colors last:border-b-0 hover:bg-[var(--ac-bg)] hover:text-ink"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Sticky top bar (§3) — replaces Sidebar. Mode tabs + logo + utility cluster. */
export default function TopBar() {
  const { mode, setMode } = useMode();
  const { toggleDrawer, setNoteOpen, setPaletteOpen } = useUi();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // APG roving-tabindex tab list: only the active tab is in the Tab order;
  // arrow keys move focus (and activate, per the standard tabs pattern —
  // automatic activation), Home/End jump to the ends, Enter/Space activate
  // explicitly.
  function activateTab(index: number) {
    const clamped = (index + TABS.length) % TABS.length;
    setMode(TABS[clamped].mode);
    tabRefs.current[clamped]?.focus();
  }

  function onTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        activateTab(index + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        activateTab(index - 1);
        break;
      case "Home":
        event.preventDefault();
        activateTab(0);
        break;
      case "End":
        event.preventDefault();
        activateTab(TABS.length - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        setMode(TABS[index].mode);
        break;
      default:
        break;
    }
  }

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-void">
      {/* `shell` (globals.css) is shared with the page container in
          (dashboard)/layout.tsx — the header is full-bleed so its bottom
          border spans the viewport, but its contents line up with the page. */}
      <div className="shell flex h-14 items-center gap-3 px-4 md:px-8">
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
          className="flex border border-line font-mono text-label uppercase tracking-[0.14em]"
        >
          {TABS.map(({ mode: tabMode, label, short }, index) => {
            const active = mode === tabMode;
            return (
              <button
                key={tabMode}
                ref={(el) => {
                  tabRefs.current[index] = el;
                }}
                type="button"
                role="tab"
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                onClick={() => setMode(tabMode)}
                onKeyDown={(event) => onTabKeyDown(event, index)}
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

        <div className="ml-auto flex items-center gap-2 font-mono text-label text-ink-faint">
          <div className="hidden items-center gap-2 md:flex">
            <Button variant="quiet" onClick={() => setNoteOpen(true)}>
              + NOTE
            </Button>
            <Button variant="quiet" aria-label="Chat" onClick={toggleDrawer}>
              CHAT
            </Button>
          </div>
          <FocusTimer />
          <OverflowMenu />
          {/* Replaces a hardcoded `● LOCAL` chip that claimed your notes stayed
              on this machine no matter which model was selected. It now names
              the model and opens the picker, and is the one control that never
              collapses — the model in use is not something to hide at a
              breakpoint. */}
          <EngineTrigger />
          <Button
            variant="quiet"
            aria-label="Command palette"
            onClick={() => setPaletteOpen(true)}
            className="normal-case tracking-normal"
          >
            [⌘K]
          </Button>
          <span
            aria-label="Current time"
            className="hidden min-h-8 items-center border border-line px-2 py-1 sm:flex"
          >
            <Clock />
          </span>
        </div>
      </div>
    </header>
  );
}
