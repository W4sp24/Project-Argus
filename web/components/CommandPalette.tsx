"use client";

import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import {
  apiFetch,
  cancelAutomationRun,
  patchAutomationWidget,
  reindexVault,
  runAutomation,
  searchVault,
  useAutomationRuns,
  useAutomations,
  useVault,
  type AutomationCard,
  type AutomationRunResult,
  type SearchResult,
} from "@/lib/api";
import { useChat } from "@/lib/chat";
import { obsidianUri } from "@/lib/citations";
import AutomationForm from "@/components/automations/AutomationForm";
import WidgetRenderer from "@/components/automations/WidgetRenderer";
import { AuthChip, OriginChip } from "@/components/automations/chips";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
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

const MODES: Mode[] = ["general", "study", "research", "code", "system", "automations"];

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
    // Management only (install/health/fix) — RUN mode below owns actually
    // running an automation.
    kind: "AUTO",
    label: "manage automations",
    hint: "/automations",
    run: (ctx) => ctx.push("/automations"),
  },
  {
    // /sources had exactly one link in the whole app: lowercase, in the
    // lowest-contrast colour in the palette, inside a panel loaded with
    // ssr:false so it was absent from the server-rendered HTML entirely. The
    // branch's flagship surface had no front door. An interface that reads as
    // keyboard-driven and cannot reach its own corpus browser is a broken
    // promise, so it belongs here.
    kind: "SOURCES",
    label: "browse sources",
    hint: "/sources",
    run: (ctx) => ctx.push("/sources"),
  },
];

/** Debounce delay (ms) before `search vault` mode fires GET /api/search. */
const SEARCH_DEBOUNCE_MS = 250;

// ---------------------------------------------------------------------------
// RUN mode — Argus's real run lifecycle, not simulated n8n nodes.
//
// n8n's public API does not stream per-node progress, so the step rail below
// tracks the five things Argus itself actually knows happened, and advances
// each step strictly on the real event that proves it — never on a timer.
// A synchronous workflow can blow through all five in under 100ms; that is
// honest, not a bug, and no artificial delay is added to make it look slower.

type RunStatus = "running" | "ok" | "failed" | "timeout";

interface RunState {
  card: AutomationCard;
  payload: Record<string, unknown>;
  isAsync: boolean;
  instanceName: string | null;
  instanceBaseUrl: string | null;
  /** performance-clock timestamps — elapsed time is always measured from
   * these, never assumed. */
  firedAt: number;
  respondedAt: number | null;
  settledAt: number | null;
  runId: string | null;
  executionId: string | null;
  executionUrl: string | null;
  status: RunStatus;
  mode: string | null;
  message: string | null;
  resultPayload: unknown;
  /** For a widget-mode result: which widget the run wrote. Named by the
   * response, because the payload alone does not identify it. */
  widgetSlug: string | null;
  widgetKind: string | null;
  widgetInstanceId: string | null;
  /** 0-based index into `stepKeysFor(isAsync)` — the one real piece of
   * mutable progress state, advanced only from real transitions in `fire`,
   * the async-poll effect, and `cancelRun`. */
  stepIndex: number;
  cancelRequested: boolean;
  cancelling: boolean;
  /** Set when the POST itself never got a response (network/backend down) —
   * distinct from an n8n-side failure, which arrives as a normal 2xx/4xx
   * RunResponse instead. */
  networkError: string | null;
}

type StepKey = "validate" | "fire" | "exec" | "awaiting" | "done";
type StepMarker = "done" | "running" | "failed" | "queued";

interface RunStep {
  key: StepKey;
  label: string;
  marker: StepMarker;
  meta: string;
}

function stepKeysFor(isAsync: boolean): StepKey[] {
  return isAsync
    ? ["validate", "fire", "exec", "awaiting", "done"]
    : ["validate", "fire", "exec", "done"];
}

/** The step index a settled run's final step lands on — one past `exec`
 * for a synchronous workflow, one past `awaiting` for an async one. */
function doneIndexFor(isAsync: boolean): number {
  return isAsync ? 4 : 3;
}

function stepLabel(key: StepKey, run: RunState): string {
  switch (key) {
    case "validate":
      return "validating fields";
    case "fire":
      return "firing trigger";
    case "exec":
      // Resolved specifically when the response carries an execution_id —
      // an ack/status-mode run (or an argus:async one, whose initial POST
      // response never includes one) legitimately never resolves this, and
      // the meta line below says so rather than pretending otherwise.
      return run.executionId ? `n8n :: exec ${run.executionId}` : "n8n :: exec";
    case "awaiting":
      return "awaiting result";
    case "done":
      return "done";
  }
}

function buildSteps(run: RunState): RunStep[] {
  return stepKeysFor(run.isAsync).map((key, index) => {
    let marker: StepMarker;
    if (index < run.stepIndex) marker = "done";
    else if (index === run.stepIndex) {
      marker = run.settledAt !== null ? (run.status === "ok" ? "done" : "failed") : "running";
    } else {
      marker = "queued";
    }
    let meta: string;
    if (marker === "running") meta = "running…";
    else if (marker === "failed") meta = "failed";
    else if (marker === "queued") meta = "queued";
    else meta = key === "exec" && !run.executionId ? "no execution id" : "done";
    return { key, label: stepLabel(key, run), marker, meta };
  });
}

function markerGlyph(marker: StepMarker): string {
  switch (marker) {
    case "done":
      return "✓";
    case "running":
      return "▸";
    case "failed":
      return "✕";
    default:
      return "·";
  }
}

const MARKER_TONE: Record<StepMarker, string> = {
  done: "border-ok text-ok",
  running: "border-warn text-warn",
  failed: "border-danger text-danger",
  queued: "border-line text-ink-faint",
};

const META_TONE: Record<StepMarker, string> = {
  done: "text-ink-faint",
  running: "text-warn",
  failed: "text-danger",
  queued: "text-ink-faint",
};

function computeProgress(run: RunState): number {
  if (run.settledAt !== null) return 100;
  const total = run.isAsync ? 5 : 4;
  return Math.min(99, Math.round(((run.stepIndex + 0.5) / total) * 100));
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  return `${(bytes / 1024).toFixed(1)}KB`;
}

function eyebrowFor(run: RunState): { text: string; tone: string } {
  if (run.settledAt === null) return { text: "● EXECUTING", tone: "text-warn" };
  if (run.cancelRequested && run.status !== "ok") {
    return { text: "● CANCELLED", tone: "text-ink-muted" };
  }
  if (run.status === "ok") return { text: "● COMPLETE", tone: "text-ok" };
  return { text: "● FAILED", tone: "text-danger" };
}

function TelemetryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-panel px-3 py-2">
      <p className="font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">{label}</p>
      <p className="truncate font-mono text-label text-ink-bright">{value}</p>
    </div>
  );
}

/** Scanline + corner-bracket chrome, scoped to the RUN panel only and
 * injected only while that mode is mounted. A plain `<style>` tag rather
 * than styled-jsx: it needs no build-time plugin, and the palette only ever
 * mounts one of these at a time. Reduced motion keeps the brackets (static,
 * not animated) and simply removes the sweeping scanline. */
const RUN_PANEL_CSS = `
.argus-run-panel { position: relative; overflow: hidden; }
.argus-corner { position: absolute; width: 11px; height: 11px; pointer-events: none; }
.argus-corner-tl { top: -1px; left: -1px; border-top: 2px solid var(--ac); border-left: 2px solid var(--ac); }
.argus-corner-tr { top: -1px; right: -1px; border-top: 2px solid var(--ac); border-right: 2px solid var(--ac); }
.argus-corner-bl { bottom: -1px; left: -1px; border-bottom: 2px solid var(--ac); border-left: 2px solid var(--ac); }
.argus-corner-br { bottom: -1px; right: -1px; border-bottom: 2px solid var(--ac); border-right: 2px solid var(--ac); }
.argus-scanline {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--ac), transparent);
  opacity: 0.55;
  animation: argus-scan 2.4s linear infinite;
  pointer-events: none;
}
@keyframes argus-scan {
  0% { transform: translateY(0); }
  100% { transform: translateY(15rem); }
}
.argus-progress-fill { transition: width 0.25s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .argus-scanline { animation: none; opacity: 0.25; }
  .argus-progress-fill { transition: none; }
}
`;

/** Flattens a widget's payload into label/value rows for Detail mode's raw
 * inspection section. Nested objects/arrays are shown as JSON — this is a
 * fallback view, not a second renderer. */
function kvRows(payload: unknown): [string, string][] {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return [];
  return Object.entries(payload as Record<string, unknown>).map(([key, value]) => [
    key,
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : value === null || value === undefined
          ? "—"
          : JSON.stringify(value),
  ]);
}

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

  // Automations: the palette's three sub-modes. `formCard` holds the
  // automation whose fields are being filled in; `confirmCard` holds a
  // pending argus:confirm gate; `run` holds a fired automation's live
  // lifecycle; `detail` holds a returned widget being read. All four mirror
  // searchMode's shape — the panel switches what it renders rather than
  // opening a second overlay, which is what keeps one Escape contract across
  // every mode.
  const { data: automations, mutate: mutateAutomations } = useAutomations();
  const [formCard, setFormCard] = useState<AutomationCard | null>(null);
  const [confirmCard, setConfirmCard] = useState<{
    card: AutomationCard;
    payload: Record<string, unknown>;
  } | null>(null);
  const [run, setRun] = useState<RunState | null>(null);
  const [detail, setDetail] = useState<{
    title: string;
    kind: string;
    payload: unknown;
    /** Named by the run response; null when the result was not a widget push. */
    slug?: string | null;
    instanceId?: string | null;
  } | null>(
    null,
  );
  // Ticks while a run is unsettled so elapsed time re-renders live — the
  // value itself is unused, only the re-render it triggers matters.
  const [, setTick] = useState(0);

  // No `?? "vault"` fallback: a guessed vault name built a link that was
  // certain to fail. `openResult` now declines rather than opening one.
  const vaultPath = vault?.path;

  // Elapsed time must be measured, not faked — this is the one clock RUN
  // mode reads from, updated on a short interval only while unsettled. Keyed
  // off a derived boolean (not `run` itself) so the interval isn't torn down
  // and rebuilt on every unrelated field update while a run is in flight.
  const runIsUnsettled = run !== null && run.settledAt === null;
  useEffect(() => {
    if (!runIsUnsettled) return;
    const id = setInterval(() => setTick((t) => t + 1), 200);
    return () => clearInterval(id);
  }, [runIsUnsettled]);

  // An argus:async workflow's initial POST response carries no final status
  // (see RunResponse.status === "running") — its eventual result is only
  // observable by polling GET /api/automations/runs, per the backend
  // contract. Polling is scoped to exactly the window where it's needed.
  const awaitingRunId =
    run && run.isAsync && run.status === "running" && run.respondedAt !== null && run.runId
      ? run.runId
      : null;
  const { data: pollRuns } = useAutomationRuns(run?.card.id, 10, {
    enabled: Boolean(awaitingRunId),
    refreshInterval: awaitingRunId ? 1500 : undefined,
  });
  useEffect(() => {
    if (!awaitingRunId || !pollRuns) return;
    const match = pollRuns.find((r) => r.id === awaitingRunId);
    if (match && match.status !== "running") {
      setRun((previous) =>
        previous && previous.runId === match.id
          ? {
              ...previous,
              status: match.status as RunStatus,
              message: match.message,
              executionId: match.execution_id ?? previous.executionId,
              resultPayload: match.payload ?? previous.resultPayload,
              stepIndex: doneIndexFor(previous.isAsync),
              settledAt: Date.now(),
            }
          : previous,
      );
    }
  }, [pollRuns, awaitingRunId]);

  // Global shortcut — listener always mounted, UI only when open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setPaletteOpen(!paletteOpen);
      } else if (event.key === "Escape" && paletteOpen) {
        // ConfirmDialog is its own portalled overlay with its own Escape
        // handler (Dialog.tsx) — let that close itself rather than also
        // tearing down the palette state underneath it.
        if (confirmCard) return;
        // Same escape-stack contract search mode has: the first Escape backs
        // out of a sub-mode, and only the second closes the palette. Losing
        // a half-filled form — or silently dropping a running execution
        // without saying so — would be its own small betrayal.
        if (run) {
          if (run.settledAt === null) {
            show(
              `automations :: ${run.card.name ?? run.card.id} keeps running on ${
                run.instanceName ?? "its instance"
              } — closing this doesn't stop it; check /automations → ACTIVITY`,
            );
          }
          setRun(null);
          return;
        }
        if (detail) {
          setDetail(null);
          return;
        }
        if (formCard) {
          setFormCard(null);
          return;
        }
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, setPaletteOpen, formCard, detail, run, confirmCard, show]);

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

  if (!paletteOpen) {
    // Never reopen into a stale sub-mode.
    if (formCard || detail || run || confirmCard) {
      if (formCard) setFormCard(null);
      if (detail) setDetail(null);
      if (run) setRun(null);
      if (confirmCard) setConfirmCard(null);
    }
    return null;
  }

  const needle = query.trim().toLowerCase();
  // Static-plus-dynamic: the constant list merged with whatever is registered
  // right now. Still a plain array built at render time — no registry, no
  // command-framework dependency, which is the module's stated principle.
  const automationActions: PaletteAction[] = (automations?.workflows ?? [])
    .filter((card: AutomationCard) => card.kind !== "none")
    .map((card: AutomationCard) => ({
      kind: "AUTO",
      label: card.name ?? card.id,
      hint: card.confirm
        ? "confirm"
        : card.fields && card.fields.length
          ? `${card.fields.length} field${card.fields.length === 1 ? "" : "s"}`
          : "run",
      run: () => undefined, // intercepted by kind in runAction, like SEARCH
    }));
  const allActions = [...PALETTE_ACTIONS, ...automationActions];
  const filtered = allActions.filter(
    (action) =>
      !needle ||
      action.label.toLowerCase().includes(needle) ||
      action.kind.toLowerCase().includes(needle) ||
      action.hint.toLowerCase().includes(needle),
  );

  const instances = automations?.instances ?? [];

  function findCard(label: string): AutomationCard | undefined {
    return (automations?.workflows ?? []).find(
      (card: AutomationCard) => (card.name ?? card.id) === label,
    );
  }

  function instanceNameFor(instanceId: string): string | undefined {
    return instances.find((instance) => instance.id === instanceId)?.name;
  }

  // The first AUTO row backed by a real workflow card — everything from here
  // down in `filtered` (they're grouped contiguously: PALETTE_ACTIONS first,
  // automationActions appended after) gets the "automations · N registered"
  // subheading. The static "manage automations" row is also kind AUTO but
  // has no matching card, so it never triggers this.
  const automationsHeaderIndex = filtered.findIndex(
    (action) => action.kind === "AUTO" && Boolean(findCard(action.label)),
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
    // Until /api/vault answers there is no vault root to build a link from.
    // Navigating anyway would hand Obsidian a URL it cannot resolve; keeping
    // the palette open lets the user try again a moment later.
    if (!vaultPath) return;
    setPaletteOpen(false);
    window.location.href = obsidianUri(vaultPath, result.source_path);
  }

  /** Fire an automation and enter RUN mode. Replaces the old behaviour of
   * merely disabling a button — every run now gets its own lifecycle view,
   * regardless of how fast it settles. */
  async function fire(card: AutomationCard, payload: Record<string, unknown>) {
    const instance = instances.find((i) => i.id === card.instance_id) ?? null;
    const firedAt = Date.now();
    setFormCard(null);
    setDetail(null);
    setRun({
      card,
      payload,
      isAsync: card.async,
      instanceName: instance?.name ?? null,
      instanceBaseUrl: instance?.base_url ?? null,
      firedAt,
      respondedAt: null,
      settledAt: null,
      runId: null,
      executionId: null,
      executionUrl: null,
      status: "running",
      mode: null,
      message: null,
      resultPayload: null,
      widgetSlug: null,
      widgetKind: null,
      widgetInstanceId: null,
      // Step 0 (validating fields) is implicitly complete: AutomationForm
      // (or the no-fields direct-fire path) already validated before this
      // function was ever called. Step 1 (firing trigger) begins now.
      stepIndex: 1,
      cancelRequested: false,
      cancelling: false,
      networkError: null,
    });

    try {
      const result: AutomationRunResult = await runAutomation(card.id, payload, card.instance_id);
      void mutateAutomations();
      const respondedAt = Date.now();
      const stillRunning = result.status === "running";
      setRun((previous) =>
        previous && previous.firedAt === firedAt
          ? {
              ...previous,
              respondedAt,
              runId: result.run_id,
              executionId: result.execution_id,
              executionUrl: result.execution_url,
              mode: result.mode,
              message: result.message,
              resultPayload: result.payload,
              widgetSlug: result.widget_slug,
              widgetKind: result.widget_kind,
              widgetInstanceId: result.instance_id,
              status: result.status as RunStatus,
              stepIndex: stillRunning ? 3 : doneIndexFor(previous.isAsync),
              settledAt: stillRunning ? null : respondedAt,
            }
          : previous,
      );
    } catch (error) {
      const respondedAt = Date.now();
      setRun((previous) =>
        previous && previous.firedAt === firedAt
          ? {
              ...previous,
              respondedAt,
              settledAt: respondedAt,
              status: "failed",
              networkError: error instanceof Error ? error.message : "could not run",
            }
          : previous,
      );
    }
  }

  /** The confirm gate lives here rather than inside `fire` so a cancelled
   * confirmation never creates a RunState at all — RUN mode should only ever
   * exist for a run that actually started. */
  function requestConfirmAndFire(card: AutomationCard, payload: Record<string, unknown>) {
    if (card.confirm) {
      setConfirmCard({ card, payload });
      return;
    }
    void fire(card, payload);
  }

  async function cancelRun() {
    if (!run || !run.runId || run.status !== "running") return;
    const runId = run.runId;
    setRun((previous) =>
      previous ? { ...previous, cancelling: true, cancelRequested: true } : previous,
    );
    try {
      const updated = await cancelAutomationRun(runId);
      setRun((previous) =>
        previous && previous.runId === updated.id
          ? {
              ...previous,
              cancelling: false,
              status: updated.status as RunStatus,
              message: updated.message,
              stepIndex: doneIndexFor(previous.isAsync),
              settledAt: Date.now(),
            }
          : previous,
      );
    } catch (error) {
      setRun((previous) => (previous ? { ...previous, cancelling: false } : previous));
      show(
        `automations :: could not cancel — ${error instanceof Error ? error.message : "unknown error"}`,
        { tone: "error" },
      );
    }
  }

  /** Shared by the CLOSE button and the Escape-stack handler above — an
   * unsettled run stays running server-side regardless, so closing early
   * says so rather than looking like it stopped anything. */
  function closeRun() {
    if (run && run.settledAt === null) {
      show(
        `automations :: ${run.card.name ?? run.card.id} keeps running on ${
          run.instanceName ?? "its instance"
        } — closing this doesn't stop it; check /automations → ACTIVITY`,
      );
    }
    setRun(null);
  }

  /** RUN mode's success box, for a widget-mode result, hands off to Detail
   * mode. The run response names the widget it wrote (`widget_slug` /
   * `widget_kind`) — the payload itself carries only the kind-specific
   * fields, so without those the result could be rendered but never acted
   * on. They are what make PIN TO DASHBOARD a real button. */
  function openDetailFromRun() {
    if (!run || run.mode !== "widget") return;
    setDetail({
      title: run.card.name ?? run.card.id,
      kind: run.widgetKind ?? "text",
      payload: run.resultPayload,
      slug: run.widgetSlug ?? null,
      instanceId: run.widgetInstanceId ?? run.card.instance_id ?? null,
    });
    setRun(null);
  }

  /** Pin the widget this run just wrote. Available only when the response
   * named it — a pin button that guessed which widget it meant would be
   * worse than one that isn't offered. */
  async function pinDetail() {
    if (!detail?.slug || !detail.instanceId) return;
    try {
      await patchAutomationWidget(detail.slug, detail.instanceId, { pinned: true });
      show(`automations :: pinned ${detail.slug} to the dashboard`);
      setDetail(null);
    } catch (error) {
      show(
        `automations :: could not pin ${detail.slug} — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    }
  }

  function runAction(action: PaletteAction) {
    if (action.kind === "SEARCH") {
      enterSearchMode();
      return;
    }
    if (action.kind === "AUTO") {
      const card = findCard(action.label);
      // The one AUTO row that is a plain navigation, not an automation.
      if (!card) {
        setPaletteOpen(false);
        action.run(ctx);
        return;
      }
      if (card.fields && card.fields.length > 0) {
        setFormCard(card);
        return;
      }
      requestConfirmAndFire(card, {});
      return;
    }
    setPaletteOpen(false);
    action.run(ctx);
  }

  const steps = run ? buildSteps(run) : [];
  const progress = run ? computeProgress(run) : 0;
  const eyebrow = run ? eyebrowFor(run) : null;
  const elapsedMs = run ? (run.settledAt ?? Date.now()) - run.firedAt : 0;
  const payloadBytes = run ? new Blob([JSON.stringify(run.payload ?? {})]).size : 0;
  const canCancel = Boolean(run && run.runId && run.status === "running" && run.settledAt === null);

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-[rgba(3,2,8,0.72)]"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) setPaletteOpen(false);
        }}
      >
        {run ? (
          <div
            role="dialog"
            aria-modal="true"
            aria-label={`Running ${run.card.name ?? run.card.id}`}
            className="argus-run-panel animate-palette mx-auto mt-[14vh] w-[46rem] max-w-[calc(100vw-2rem)] border border-lineHi bg-panel px-5 py-4"
          >
            <style>{RUN_PANEL_CSS}</style>
            <span className="argus-corner argus-corner-tl" aria-hidden="true" />
            <span className="argus-corner argus-corner-tr" aria-hidden="true" />
            <span className="argus-corner argus-corner-bl" aria-hidden="true" />
            <span className="argus-corner argus-corner-br" aria-hidden="true" />
            {run.settledAt === null && <span className="argus-scanline" aria-hidden="true" />}

            <div className="mb-3 flex items-start justify-between gap-3">
              <span
                className={`font-mono text-meta uppercase tracking-[0.18em] ${eyebrow?.tone ?? ""}`}
              >
                {eyebrow?.text}
              </span>
              <span className="font-mono text-display tabular-nums text-ink-bright">
                {progress}%
              </span>
            </div>

            <p className="truncate text-lead text-ink-bright">{run.card.name ?? run.card.id}</p>
            <p className="mb-3 truncate font-mono text-meta text-ink-faint">
              {run.instanceName ?? "unknown instance"}
              {run.instanceBaseUrl ? ` · ${run.instanceBaseUrl}` : ""}
            </p>

            <div className="mb-3 h-1.5 w-full border border-line bg-sunken">
              <div
                className="argus-progress-fill h-full bg-[var(--ac)]"
                style={{ width: `${progress}%` }}
              />
            </div>

            <ul className="mb-4 flex flex-col">
              {steps.map((step, i) => (
                <li key={step.key} className="relative flex gap-3 pb-3 last:pb-0">
                  {i < steps.length - 1 && (
                    <span
                      className="absolute left-[7px] top-[18px] bottom-0 w-px bg-line"
                      aria-hidden="true"
                    />
                  )}
                  <span
                    className={`z-10 flex h-4 w-4 shrink-0 items-center justify-center border font-mono text-micro ${MARKER_TONE[step.marker]}`}
                    aria-hidden="true"
                  >
                    {markerGlyph(step.marker)}
                  </span>
                  <span className="flex min-w-0 flex-1 items-baseline justify-between gap-2">
                    <span className="truncate font-mono text-label text-ink">{step.label}</span>
                    <span
                      className={`shrink-0 font-mono text-meta ${META_TONE[step.marker]}`}
                    >
                      {step.meta}
                    </span>
                  </span>
                </li>
              ))}
            </ul>

            <div className="mb-4 grid grid-cols-4 gap-px border border-line bg-line">
              <TelemetryCell label="ELAPSED" value={formatElapsed(elapsedMs)} />
              <TelemetryCell label="STEP" value={`${Math.min(run.stepIndex + 1, steps.length)}/${steps.length}`} />
              <TelemetryCell label="PAYLOAD" value={formatBytes(payloadBytes)} />
              <TelemetryCell label="INSTANCE" value={run.instanceName ?? "—"} />
            </div>

            {run.settledAt !== null && run.status === "ok" && (
              <div className="mb-4 border border-ok px-3 py-2">
                <p className="font-mono text-meta uppercase tracking-[0.12em] text-ok">success</p>
                <p className="mt-1 text-label text-ink">{run.message ?? "workflow completed."}</p>
                {run.mode === "widget" && (
                  <button
                    type="button"
                    onClick={openDetailFromRun}
                    className="mt-2 font-mono text-meta uppercase tracking-[0.12em] text-[var(--ac)] hover:underline"
                  >
                    VIEW DETAIL →
                  </button>
                )}
              </div>
            )}

            {run.settledAt !== null && run.status !== "ok" && (
              <div className="mb-4 border border-danger px-3 py-2">
                <p className="font-mono text-meta uppercase tracking-[0.12em] text-danger">
                  {run.cancelRequested
                    ? "cancelled"
                    : run.status === "timeout"
                      ? "timed out"
                      : "failed"}
                </p>
                <p className="mt-1 text-label text-ink">
                  {run.networkError ?? run.message ?? "the run did not complete."}
                </p>
                <p className="mt-1 font-mono text-meta text-ink-faint">
                  find it in /automations → ACTIVITY
                  {run.executionUrl ? " · or open the execution in n8n" : ""}
                </p>
              </div>
            )}

            <div className="flex items-center gap-2 border-t border-line pt-3">
              <Button
                variant="danger"
                size="md"
                disabled={!canCancel}
                onClick={() => void cancelRun()}
              >
                {run.cancelling ? "CANCELLING…" : "CANCEL RUN"}
              </Button>
              <Button variant="secondary" size="md" className="ml-auto" onClick={closeRun}>
                CLOSE
              </Button>
            </div>
          </div>
        ) : (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          className="animate-palette mx-auto mt-[16vh] w-[520px] max-w-[calc(100vw-2rem)] border border-lineHi bg-panel"
        >
          {detail ? (
            // Detail mode: a returned widget, read once and thrown away.
            // Nothing is persisted unless it is explicitly pinned, so a
            // one-off lookup never clutters the dashboard.
            <div className="px-4 py-3">
              <div className="mb-3 flex items-center gap-2">
                <p className="font-mono text-meta uppercase tracking-[0.14em] text-[var(--ac)]">
                  DETAIL
                </p>
                <p className="min-w-0 flex-1 truncate text-label text-ink-muted">
                  {detail.title}
                </p>
                <button
                  type="button"
                  onClick={() => setDetail(null)}
                  className="font-mono text-micro uppercase tracking-[0.14em] text-ink-faint hover:text-ink"
                >
                  ESC
                </button>
              </div>

              <WidgetRenderer kind={detail.kind} payload={detail.payload} />

              <div className="mt-3 border border-line">
                {kvRows(detail.payload).length === 0 ? (
                  <p className="px-3 py-2 text-label text-ink-faint">
                    No flat fields in this payload.
                  </p>
                ) : (
                  kvRows(detail.payload).map(([key, value]) => (
                    <div
                      key={key}
                      className="flex items-start justify-between gap-3 border-b border-line px-3 py-2 last:border-b-0"
                    >
                      <span className="shrink-0 font-mono text-meta uppercase tracking-[0.08em] text-ink-faint">
                        {key}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-right text-label text-ink">
                        {value}
                      </span>
                    </div>
                  ))
                )}
              </div>

              <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!detail.slug || !detail.instanceId}
                  onClick={() => void pinDetail()}
                  title={
                    detail.slug
                      ? `Keep ${detail.slug} on the dashboard`
                      : "This result was not a widget push, so there is nothing to pin."
                  }
                >
                  PIN TO DASHBOARD
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  className="ml-auto"
                  onClick={() => setDetail(null)}
                >
                  CLOSE
                </Button>
              </div>
            </div>
          ) : formCard ? (
            <AutomationForm
              card={formCard}
              busy={confirmCard !== null}
              onCancel={() => setFormCard(null)}
              onSubmit={(payload) => requestConfirmAndFire(formCard, payload)}
            />
          ) : (
          <>
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
              {filtered.map((action, i) => {
                const card = action.kind === "AUTO" ? findCard(action.label) : undefined;
                return (
                  <Fragment key={`${action.kind}-${action.label}`}>
                    {i === automationsHeaderIndex && (
                      <li className="px-4 pb-1 pt-3 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                        {`automations · ${automationActions.length} registered`}
                      </li>
                    )}
                    <li>
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
                        {card && instances.length > 1 && (
                          <OriginChip
                            instanceId={card.instance_id}
                            name={instanceNameFor(card.instance_id)}
                          />
                        )}
                        {card?.basic_auth && <AuthChip />}
                        <span className="shrink-0 font-mono text-meta text-ink-faint">
                          {action.hint}
                        </span>
                      </button>
                    </li>
                  </Fragment>
                );
              })}
            </ul>
          )}
          </>
          )}
        </div>
        )}
      </div>
      {confirmCard && (
        <ConfirmDialog
          label={`Run ${confirmCard.card.name ?? confirmCard.card.id}`}
          message={`Run "${confirmCard.card.name ?? confirmCard.card.id}" now?`}
          detail="Tagged argus:confirm — n8n treats this workflow as consequential."
          confirmLabel="RUN"
          tone="primary"
          onConfirm={() => {
            const pending = confirmCard;
            setConfirmCard(null);
            void fire(pending.card, pending.payload);
          }}
          onCancel={() => setConfirmCard(null)}
        />
      )}
    </>
  );
}
