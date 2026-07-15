"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import { useChat } from "@/lib/chat";
import { MODE_ROUTES, type Mode } from "@/lib/mode";
import { useUi } from "@/lib/ui";

/**
 * Command palette (§6, flags.palette = "enabled"). Global meta/ctrl+K
 * toggles, Escape closes. Overlay rgba(3,2,8,.72) — NO blur; 520px panel
 * with a .18s rise; rows are `KIND · label · hint`; plain substring filter.
 * Renders nothing while closed (§10) — only the keydown listener persists.
 */

export interface PaletteContext {
  push: (route: string) => void;
  toast: (message: string) => void;
  sendChat: (text: string) => void;
  openDrawer: () => void;
  openNote: () => void;
  startFocus: () => void;
}

export interface PaletteAction {
  kind: string;
  label: string;
  hint: string;
  /** Marks actions whose real backend doesn't exist yet (§8). */
  preview?: boolean;
  run: (ctx: PaletteContext) => void;
}

const MODES: Mode[] = ["general", "study", "research", "code", "system"];

/** Plain exported array — no command framework dependency (§6). */
export const PALETTE_ACTIONS: PaletteAction[] = [
  ...MODES.map((mode) => ({
    kind: "MODE",
    label: `switch to ${mode}`,
    hint: MODE_ROUTES[mode],
    run: (ctx: PaletteContext) => ctx.push(MODE_ROUTES[mode]),
  })),
  {
    kind: "AGENT",
    label: "generate briefing",
    hint: "compose + write today's briefing",
    run: (ctx) => {
      ctx.toast("briefing :: generating…");
      fetch("/api/briefing/run", { method: "POST" })
        .then((response) =>
          ctx.toast(
            response.ok
              ? "briefing :: written to today's daily note"
              : "briefing :: failed — see backend logs",
          ),
        )
        .catch(() => ctx.toast("briefing :: failed — is the backend running?"));
    },
  },
  {
    kind: "CHAT",
    label: "/plan tomorrow",
    hint: "planner → review queue",
    run: (ctx) => {
      ctx.openDrawer();
      ctx.sendChat("/plan tomorrow");
    },
  },
  {
    kind: "FOCUS",
    label: "start focus session",
    hint: "25:00 countdown",
    run: (ctx) => ctx.startFocus(),
  },
  {
    kind: "CHAT",
    label: "open chat",
    hint: "drawer",
    run: (ctx) => ctx.openDrawer(),
  },
  {
    kind: "NOTE",
    label: "add note",
    hint: "quick capture → 00-Inbox",
    run: (ctx) => ctx.openNote(),
  },
  {
    // No reindex HTTP endpoint exists — `argus reindex` is CLI-only
    // (backend/cli.py → VaultIndex.reindex_all). Preview until the backend
    // branch exposes one.
    kind: "INDEX",
    label: "reindex",
    hint: "preview",
    preview: true,
    run: (ctx) => ctx.toast("reindex :: arrives with the backend branch"),
  },
];

export default function CommandPalette() {
  const router = useRouter();
  const { show } = useToast();
  const { send } = useChat();
  const { paletteOpen, setPaletteOpen, setDrawerOpen, setNoteOpen, startFocus } = useUi();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // Global shortcut — listener always mounted, UI only when open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setPaletteOpen(!paletteOpen);
      } else if (event.key === "Escape" && paletteOpen) {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, setPaletteOpen]);

  // Focus management: remember the opener, trap focus in the input while
  // open (rows are arrow-key driven), restore focus on close.
  useEffect(() => {
    if (paletteOpen) {
      restoreRef.current = document.activeElement as HTMLElement | null;
      setQuery("");
      setActive(0);
      // next frame: the panel mounts in this same commit
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      restoreRef.current?.focus?.();
      restoreRef.current = null;
    }
  }, [paletteOpen]);

  if (!paletteOpen) return null;

  const needle = query.trim().toLowerCase();
  const filtered = PALETTE_ACTIONS.filter(
    (action) =>
      !needle ||
      action.label.toLowerCase().includes(needle) ||
      action.kind.toLowerCase().includes(needle) ||
      action.hint.toLowerCase().includes(needle),
  );

  const ctx: PaletteContext = {
    push: (route) => router.push(route),
    toast: show,
    sendChat: send,
    openDrawer: () => setDrawerOpen(true),
    openNote: () => setNoteOpen(true),
    startFocus,
  };

  function runAction(action: PaletteAction) {
    setPaletteOpen(false);
    action.run(ctx);
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-[rgba(3,2,8,0.72)]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setPaletteOpen(false);
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="animate-palette mx-auto mt-[16vh] w-[520px] max-w-[calc(100vw-2rem)] border border-lineHi bg-panel"
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((i) => Math.min(i + 1, filtered.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              const action = filtered[active];
              if (action) runAction(action);
            } else if (event.key === "Tab") {
              // focus trap: the input is the palette's single tab stop
              event.preventDefault();
            }
          }}
          placeholder="type a command…"
          aria-label="Filter commands"
          className="w-full border-b border-line bg-sunken px-4 py-3 font-mono text-[13px] text-ink placeholder:text-ink-faint focus:outline-none"
        />
        <ul className="max-h-[50vh] overflow-y-auto py-1">
          {filtered.length === 0 && (
            <li className="px-4 py-3 font-mono text-[11px] text-ink-faint">no matches</li>
          )}
          {filtered.map((action, i) => (
            <li key={`${action.kind}-${action.label}`}>
              <button
                type="button"
                tabIndex={-1}
                onClick={() => runAction(action)}
                onMouseEnter={() => setActive(i)}
                className={`flex w-full items-center gap-2 px-4 py-2 text-left transition-colors ${
                  i === active ? "bg-[var(--ac-bg)]" : ""
                }`}
              >
                <span className="w-14 shrink-0 font-mono text-[9.5px] uppercase tracking-[0.14em] text-[var(--ac)]">
                  {action.kind}
                </span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                  {action.label}
                </span>
                {action.preview && (
                  <span className="border border-[#3d2f66] px-1 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">
                    PREVIEW
                  </span>
                )}
                <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                  {action.hint}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
