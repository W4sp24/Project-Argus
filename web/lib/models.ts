"use client";

import { useSyncExternalStore } from "react";

/**
 * Model selector persistence (§7): the currently-selected model name.
 *
 * The registry itself (built-ins + user-added local models) now comes from
 * the real `GET /api/models` (`useModels()` in lib/api.ts) — the previous
 * `localStorage["argus-local-models"]` mirror is gone (Phase H): once the
 * backend registry existed there was no reason to keep a second, driftable
 * copy of the same data in the browser. `ModelSelect` falls back to
 * `BUILTIN_MODELS` when the API is unreachable (offline / backend down).
 * Selection persists to `localStorage["argus-model"]` and is sent as a
 * `model` field on every chat WS frame.
 */
export interface ModelEntry {
  name: string;
  kind: "api" | "local";
  endpoint?: string;
}

/** Offline fallback — mirrors backend/config.py's DEFAULT_MODELS. */
export const BUILTIN_MODELS: ModelEntry[] = [
  { name: "claude-sonnet-4", kind: "api" }, // default
  { name: "claude-haiku", kind: "api" },
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
