"use client";

import { useEffect, useId, useRef, useState } from "react";
import Sparkline from "@/components/charts/Sparkline";
import { agentColor, compactTokens } from "@/lib/agentPalette";

export interface AgentOption {
  id: string;
  label: string;
  detected: boolean;
  total: number;
  /** Why it is unavailable — printed on the disabled row itself. */
  hint: string;
  trend: number[];
}

/**
 * The agent selector.
 *
 * A dropdown rather than a chip rail because the list is now open-ended — three
 * built-ins plus however many the user registers — and a rail stops fitting at
 * about four. What a rail did give you was comparison at a glance, so the menu
 * pays that back: every row carries a total and a sparkline, which makes
 * *opening* the selector the comparison view.
 *
 * Unavailable agents are dimmed and inert, never hidden. That is only
 * acceptable because the row states its own reason — an agent you cannot click
 * still tells you why, so nothing is stuck behind an interaction you are not
 * allowed to perform.
 *
 * Positioned `absolute`, not portalled: `Dialog` is for modals and this is not
 * one. It anchors correctly inside a `Panel` because the wrapper below is the
 * nearest positioned ancestor.
 */
export default function AgentSelect({
  options,
  value,
  onChange,
}: {
  options: AgentOption[];
  value: string;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const selectable = options.filter((option) => option.detected);
  const selected = options.find((option) => option.id === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function choose(id: string) {
    onChange(id);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function openAt(index: number) {
    setActive(Math.max(0, index));
    setOpen(true);
  }

  /** Arrow keys walk only the selectable rows — a disabled one is never landed on. */
  function move(delta: number) {
    if (selectable.length === 0) return;
    setActive((current) => {
      const next = current + delta;
      if (next < 0) return selectable.length - 1;
      if (next >= selectable.length) return 0;
      return next;
    });
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      if (open) {
        event.stopPropagation(); // an open menu eats Escape before any dialog does
        setOpen(false);
        triggerRef.current?.focus();
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openAt(selectable.findIndex((option) => option.id === value));
        return;
      }
      move(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (open && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      const option = selectable[active];
      if (option) choose(option.id);
    }
  }

  return (
    <div ref={wrapRef} className="relative" onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? setOpen(false) : openAt(selectable.findIndex((o) => o.id === value)))}
        className={`flex w-full min-w-[13rem] items-center gap-2 border px-2.5 py-1.5 text-left transition-colors ${
          open ? "border-lineHi bg-sunken" : "border-line hover:border-lineHi"
        }`}
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: selected ? agentColor(selected.id) : "var(--ac)" }}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-micro uppercase tracking-[0.14em] text-ink-bright">
          {selected?.label ?? "—"}
        </span>
        <span className="shrink-0 font-mono text-micro tabular-nums text-ink-muted">
          {selected ? compactTokens(selected.total) : "—"}
        </span>
        <span aria-hidden className="shrink-0 font-mono text-micro text-ink-faint">
          {open ? "▴" : "▾"}
        </span>
      </button>

      {open && (
        // `min-w-[16rem]`, not the old `min-w-[21rem]`: this panel also renders
        // in the 340px dashboard/code rail (300px after padding), where a
        // 336px-floor popover overflowed the panel outright. A viewport
        // breakpoint can't restore a wider floor for the /system placement
        // instead, because /system's full-width panel and the narrow rail
        // both live on the same wide viewport — `sm:` can't tell them apart.
        // `max-w-[calc(100vw-2rem)]` is the same narrow-window guard the
        // engine picker dialog uses.
        <ul
          id={listId}
          role="listbox"
          aria-label="Agent"
          className="animate-palette absolute left-0 top-full z-30 mt-1 max-h-[19rem] w-full min-w-[16rem] max-w-[calc(100vw-2rem)] overflow-y-auto border border-lineHi bg-panel py-1 shadow-xl"
        >
          {options.map((option) => {
            const index = selectable.indexOf(option);
            const isActive = index >= 0 && index === active;
            const isSelected = option.id === value;

            if (!option.detected) {
              return (
                <li
                  key={option.id}
                  role="option"
                  aria-disabled="true"
                  aria-selected={false}
                  className="cursor-not-allowed select-none px-2.5 py-1.5 opacity-40"
                >
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="h-1.5 w-1.5 shrink-0 rounded-full border border-ink-faint"
                    />
                    <span className="min-w-0 flex-1 truncate font-mono text-micro uppercase tracking-[0.14em] text-ink-muted">
                      {option.label}
                    </span>
                    <span className="shrink-0 font-mono text-micro uppercase tracking-[0.1em] text-ink-faint">
                      not detected
                    </span>
                  </div>
                  {/* The reason lives here because the row cannot be clicked to
                      reveal it anywhere else. */}
                  <p className="mt-0.5 pl-3.5 text-micro leading-relaxed text-ink-faint">
                    {option.hint}
                  </p>
                </li>
              );
            }

            return (
              <li key={option.id} role="option" aria-selected={isSelected}>
                <button
                  type="button"
                  onClick={() => choose(option.id)}
                  onMouseEnter={() => setActive(index)}
                  className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors ${
                    isActive ? "bg-sunken" : ""
                  }`}
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: agentColor(option.id) }}
                  />
                  <span
                    className={`min-w-0 flex-1 truncate font-mono text-micro uppercase tracking-[0.14em] ${
                      isSelected ? "text-ink-bright" : "text-ink-muted"
                    }`}
                  >
                    {option.label}
                  </span>
                  <span
                    className="shrink-0 font-mono text-micro tabular-nums text-ink"
                    title={`${option.total.toLocaleString()} tokens`}
                  >
                    {compactTokens(option.total)}
                  </span>
                  <Sparkline values={option.trend} color={agentColor(option.id)} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
