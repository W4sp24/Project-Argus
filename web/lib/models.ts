"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { useToast } from "@/components/Toast";
import { useModels } from "@/lib/api";

/**
 * Model selector persistence (§7): the currently-selected model name.
 *
 * The registry itself (built-ins + user-added local models) now comes from
 * the real `GET /api/models` (`useModels()` in lib/api.ts) — the previous
 * `localStorage["argus-local-models"]` mirror is gone (Phase H): once the
 * backend registry existed there was no reason to keep a second, driftable
 * copy of the same data in the browser. `EnginePicker` falls back to
 * `BUILTIN_MODELS` when the API is unreachable (offline / backend down).
 * Selection persists to `localStorage["argus-model"]` and is sent as a
 * `model` field on every chat WS frame.
 */
export interface ModelEntry {
  name: string;
  /**
   * `local` means the model runs on this machine and notes never leave it;
   * `api` means excerpts go to a provider. Derived from the backend's `local`
   * flag, not from `builtin` — a hosted open-weight endpoint is every bit as
   * much a network hop as Claude is.
   */
  kind: "api" | "local";
  endpoint?: string;
}

/** Offline fallback — mirrors backend/config.py's DEFAULT_MODELS. */
export const BUILTIN_MODELS: ModelEntry[] = [
  { name: "claude-sonnet-5", kind: "api" }, // default
  { name: "claude-haiku-4-5-20251001", kind: "api" },
];

export const DEFAULT_MODEL = BUILTIN_MODELS[0].name;

const MODEL_KEY = "argus-model";

/** Currently selected model name — safe to call anywhere client-side. */
export function selectedModel(): string {
  try {
    return window.localStorage.getItem(MODEL_KEY) || DEFAULT_MODEL;
  } catch {
    return DEFAULT_MODEL;
  }
}

// Tiny external store so every surface showing `{model}` (drawer header,
// /chat header, footer status line) stays in sync without prop drilling.
const listeners = new Set<() => void>();

export function setModel(name: string): void {
  try {
    window.localStorage.setItem(MODEL_KEY, name);
  } catch {
    // best-effort persistence (private browsing etc.) — in-memory state still updates
  }
  listeners.forEach((listener) => listener());
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  // storage events keep tabs in sync
  window.addEventListener("storage", callback);
  return () => {
    listeners.delete(callback);
    window.removeEventListener("storage", callback);
  };
}

/** SSR-safe reactive selected-model name (server snapshot = default). */
export function useSelectedModel(): string {
  return useSyncExternalStore(subscribe, selectedModel, () => DEFAULT_MODEL);
}

/**
 * Reconciles the persisted choice with the real registry. Mounted once, in the
 * dashboard layout.
 *
 * Two ways the two could disagree, both silent before this: deleting the
 * selected model on /system left `localStorage` naming an entry the backend no
 * longer knows, so the next question was sent against a dead model and failed
 * somewhere deep in the agent; and the backend's own DEFAULT (set from the
 * MODELS panel) had no bearing on what the browser had stored, so the two
 * could point at different models indefinitely.
 *
 * Falls back to the registry's default and says so — quietly changing which
 * model answers your questions is not something to do without telling anyone.
 */
export function useModelSync(): void {
  const { data } = useModels();
  const { show } = useToast();
  const selected = useSelectedModel();
  const announced = useRef<string | null>(null);

  useEffect(() => {
    if (!data || data.length === 0) return;
    if (data.some((model) => model.name === selected)) return;

    const fallback = data.find((model) => model.default) ?? data[0];
    if (fallback.name === selected) return;

    // One notice per stale name, not one per revalidation.
    if (announced.current !== selected) {
      announced.current = selected;
      show(`model :: ${selected} is no longer registered — using ${fallback.name}`, {
        tone: "error",
      });
    }
    setModel(fallback.name);
  }, [data, selected, show]);
}
