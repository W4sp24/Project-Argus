"use client";

import { useSyncExternalStore } from "react";

/**
 * Model registry (§7): two API built-ins plus user-registered local models.
 *
 * Local models are written by the /system MODELS panel into
 * localStorage["argus-local-models"] as {name, endpoint} entries — read
 * tolerantly here (that panel is owned by a parallel branch; malformed or
 * missing data must never break the selector). The selected model persists
 * to localStorage["argus-model"] and is sent as a `model` field on every
 * chat WS frame (the backend ignores unknown frame fields today; routing
 * local models to their endpoint is the backend's concern — flags.localModels
 * stays "preview" until then).
 */
export interface ModelEntry {
  name: string;
  kind: "api" | "local";
  endpoint?: string;
}

export const BUILTIN_MODELS: ModelEntry[] = [
  { name: "claude-sonnet-4", kind: "api" }, // default
  { name: "claude-haiku", kind: "api" },
];

export const DEFAULT_MODEL = BUILTIN_MODELS[0].name;

const MODEL_KEY = "argus-model";
const LOCAL_MODELS_KEY = "argus-local-models";

/** User-added local models — tolerant of absent/malformed storage. */
export function readLocalModels(): ModelEntry[] {
  try {
    const raw = window.localStorage.getItem(LOCAL_MODELS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (entry): entry is { name: string; endpoint?: unknown } =>
          typeof entry === "object" &&
          entry !== null &&
          typeof (entry as { name?: unknown }).name === "string" &&
          ((entry as { name: string }).name.trim().length > 0),
      )
      .map((entry) => ({
        name: entry.name.trim(),
        kind: "local" as const,
        endpoint: typeof entry.endpoint === "string" ? entry.endpoint : undefined,
      }));
  } catch {
    return [];
  }
}

/** Built-ins first, then local models. Client-only (reads localStorage). */
export function listModels(): ModelEntry[] {
  return [...BUILTIN_MODELS, ...readLocalModels()];
}

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
  // storage events keep tabs in sync and pick up /system MODELS panel writes
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
