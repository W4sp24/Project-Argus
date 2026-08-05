"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import { apiFetch, reindexVault, searchVault, useVault, type SearchResult } from "@/lib/api";
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

/** Reindex polling cadence and giving-up point, for the palette's own toast —
 * the run itself has no fixed deadline server-side, this is just how long the
 * palette keeps watching before telling the user to check back later. */
const INDEX_POLL_MS = 700;
const INDEX_POLL_TIMEOUT_MS = 5 * 60_000;

interface IndexStatusPayload {
  indexing: boolean;
  chunks: number;
  files: number;
  last_error: string | null;
}

/** Poll GET /api/index/status until the background rebuild finishes, then
 * toast the real outcome (chunk/file counts, or the real error) — replacing
 * the old fake "arrives with the backend branch" toast entirely. Not a hook:
 * PALETTE_ACTIONS is a plain array, not a component, so this just recurses
 * with setTimeout the same way installModel's NDJSON reader loops manually. */
async function pollIndexStatus(ctx: PaletteContext, startedAt: number): Promise<void> {
  let response: Response;
  try {
    response = await apiFetch("/api/index/status");
  } catch {
    ctx.toast("reindex :: failed — is the backend running?");
    return;
  }
  if (!response.ok) {
    ctx.toast("reindex :: status check failed — see backend logs");
    return;
  }
  const status = (await response.json()) as IndexStatusPayload;
  if (status.indexing) {
    if (Date.now() - startedAt > INDEX_POLL_TIMEOUT_MS) {
      ctx.toast("reindex :: still running — check back later");
      return;
    }
    setTimeout(() => void pollIndexStatus(ctx, startedAt), INDEX_POLL_MS);
    return;
  }
  ctx.toast(
    status.last_error
      ? `reindex :: failed — ${status.last_error}`
      : `reindex :: done — ${status.chunks} chunks from ${status.files} files`,
  );
}

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
      apiFetch("/api/briefing/run", { method: "POST" })
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
    // Distinct from "open chat": fast, non-agentic hybrid vector+BM25
    // citations only, no generated answer — GET /api/search (backend/search_api.py).
    // CommandPalette intercepts selection of this action to switch the panel
    // into an inline search-results mode instead of calling `run` directly.
    kind: "SEARCH",
    label: "search vault",
    hint: "cited semantic search",
    run: (ctx) => ctx.toast("search :: type a query, press enter"),
  },
  {
    // POST /api/index/reindex — runs on a background thread; the palette
    // polls GET /api/index/status until it finishes, then toasts the real
    // outcome. See pollIndexStatus above.
    kind: "INDEX",
    label: "reindex",
    hint: "rebuild the vault index",
    run: (ctx) => {
      ctx.toast("reindex :: starting…");
      reindexVault()
        .then(() => pollIndexStatus(ctx, Date.now()))
        .catch(() => ctx.toast("reindex :: failed — is the backend running?"));
    },
  },
  {
    // Management only (install/health/fix) — a later chunk owns the
    // palette's own form/detail modes for actually running an automation.
    kind: "AUTO",
    label: "manage automations",
    hint: "/automations",
    run: (ctx) => ctx.push("/automations"),
  },
];

/** Debounce delay (ms) before `search vault` mode fires GET /api/search. */
const SEARCH_DEBOUNCE_MS = 250;

export default function CommandPalette() {
  const router = useRouter();
  const { show } = useToast();
  const { send } = useChat();
  const { paletteOpen, setPaletteOpen, setDrawerOpen, setNoteOpen, startFocus } = useUi();
  const { data: vault } = useVault();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // "search vault" mode: the same input drives a live query instead of
  // filtering PALETTE_ACTIONS, and the list below renders cited results.
  const [searchMode, setSearchMode] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const vaultName = vault?.name ?? "vault";

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
      setSearchMode(false);
      setSearchResults([]);
      // next frame: the panel mounts in this same commit
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      restoreRef.current?.focus?.();
      restoreRef.current = null;
    }
  }, [paletteOpen]);

  // Debounced live search while in search mode.
  useEffect(() => {
    if (!searchMode) return;
    if (searchTimer.current) clearTimeout(searchTimer.current);
    const needleText = query.trim();
    if (!needleText) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    searchTimer.current = setTimeout(() => {
      searchVault(needleText)
        .then((results) => setSearchResults(results))
        .catch(() => setSearchResults([]))
        .finally(() => setSearching(false));
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, searchMode]);

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

  function enterSearchMode() {
    setSearchMode(true);
    setQuery("");
    setActive(0);
    setSearchResults([]);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  function openResult(result: SearchResult) {
    setPaletteOpen(false);
    window.location.href = `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(result.source_path)}`;
  }

  function runAction(action: PaletteAction) {
    if (action.kind === "SEARCH") {
      enterSearchMode();
      return;
    }
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
            if (searchMode) {
              if (event.key === "Escape") {
                // First Escape backs out of search mode; only a second
                // Escape (handled by the window-level listener) closes.
                event.preventDefault();
                event.stopPropagation();
                setSearchMode(false);
                setQuery("");
                setActive(0);
              } else if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((i) => Math.min(i + 1, searchResults.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((i) => Math.max(i - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                const result = searchResults[active];
                if (result) openResult(result);
              }
              return;
            }
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
          placeholder={searchMode ? "search the vault…" : "type a command…"}
          aria-label={searchMode ? "Search vault" : "Filter commands"}
          // No `focus:outline-none` here: Tailwind emits it at specificity
          // (0,2,0), which outranks the bare `:focus-visible` rule in
          // globals.css — this input had no visible focus ring at all.
          className="w-full border-b border-line bg-sunken px-4 py-3 font-mono text-body text-ink placeholder:text-ink-faint"
        />
        {searchMode ? (
          <ul className="max-h-[50vh] overflow-y-auto py-1">
            {searching && (
              <li className="px-4 py-3 font-mono text-label text-ink-faint">searching…</li>
            )}
            {!searching && query.trim() === "" && (
              <li className="px-4 py-3 font-mono text-label text-ink-faint">
                type to search — esc to go back
              </li>
            )}
            {!searching && query.trim() !== "" && searchResults.length === 0 && (
              <li className="px-4 py-3 font-mono text-label text-ink-faint">no matches</li>
            )}
            {!searching &&
              searchResults.map((result, i) => (
                <li key={`${result.source_path}-${i}`}>
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={() => openResult(result)}
                    onMouseEnter={() => setActive(i)}
                    className={`flex w-full flex-col gap-0.5 px-4 py-2 text-left transition-colors ${
                      i === active ? "bg-[var(--ac-bg)]" : ""
                    }`}
                  >
                    <span className="flex w-full items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-body text-ink">
                        {result.title || result.source_path}
                      </span>
                      <span className="shrink-0 font-mono text-meta text-ink-faint">
                        {result.source_path}
                      </span>
                    </span>
                    <span className="line-clamp-2 text-label text-ink-faint">
                      {result.snippet}
                    </span>
                  </button>
                </li>
              ))}
          </ul>
        ) : (
          <ul className="max-h-[50vh] overflow-y-auto py-1">
            {filtered.length === 0 && (
              <li className="px-4 py-3 font-mono text-label text-ink-faint">no matches</li>
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
                  <span className="w-14 shrink-0 font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)]">
                    {action.kind}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-body text-ink">
                    {action.label}
                  </span>
                  {action.preview && (
                    <span className="border border-[#3d2f66] px-1 py-px font-mono text-micro uppercase tracking-[0.16em] text-[#8b7bc0]">
                      PREVIEW
                    </span>
                  )}
                  <span className="shrink-0 font-mono text-meta text-ink-faint">
                    {action.hint}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
