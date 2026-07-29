"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * The renderer half of the desktop auto-updater.
 *
 * `desktop/lib/updater.js` drives electron-updater in the main process and
 * forwards every state change to the window over the `update:event` IPC
 * channel; `desktop/preload.js` exposes it as `window.argus.onUpdate`. Nothing
 * happens without a listener here: `autoDownload` and `autoInstallOnAppQuit`
 * are both off on purpose, so a build whose renderer ignores these events can
 * never update itself.
 */

/** Payloads emitted by `desktop/lib/updater.js`. */
export type UpdateEvent =
  | { type: "available"; version: string }
  | { type: "none" }
  | { type: "progress"; percent: number; transferred: number; total: number }
  | { type: "ready"; version: string }
  | { type: "error"; message: string };

export type UpdateStatus = "idle" | "none" | "available" | "downloading" | "ready" | "error";

interface UpdateBridge {
  onUpdate: (callback: (event: UpdateEvent) => void) => () => void;
  downloadUpdate: () => void;
  installUpdate: () => void;
  checkForUpdates: () => void;
}

/**
 * The preload bridge, or null outside the Electron shell — the dashboard also
 * runs as a plain Next app (and under Playwright), where `window.argus` does
 * not exist and every update surface must render nothing.
 */
function bridge(): UpdateBridge | null {
  if (typeof window === "undefined") return null;
  const argus = (window as unknown as { argus?: Partial<UpdateBridge> }).argus;
  if (!argus?.onUpdate || !argus.downloadUpdate || !argus.installUpdate) return null;
  return argus as UpdateBridge;
}

export interface UpdateState {
  status: UpdateStatus;
  /** The offered version, once the updater has named one. */
  version: string | null;
  percent: number;
  /** Bytes so far / total for this download. Zero until the first tick. */
  transferred: number;
  total: number;
  message: string | null;
}

const INITIAL: UpdateState = {
  status: "idle",
  version: null,
  percent: 0,
  transferred: 0,
  total: 0,
  message: null,
};

function reduce(prev: UpdateState, event: UpdateEvent): UpdateState {
  switch (event.type) {
    case "available":
      return { ...INITIAL, status: "available", version: event.version };
    case "progress":
      return {
        ...prev,
        status: "downloading",
        percent: event.percent,
        transferred: event.transferred,
        total: event.total,
      };
    case "ready":
      return { ...prev, status: "ready", version: event.version, percent: 100, message: null };
    case "none":
      return { ...INITIAL, status: "none" };
    case "error":
      return { ...prev, status: "error", message: event.message };
  }
}

/**
 * Identity of the *current* prompt, for dismissal purposes. Empty means "not
 * dismissible": a download in flight and a staged install both have to stay on
 * screen, because dismissing them would strand the user with no way back to
 * the restart button.
 *
 * Keying on content rather than a boolean is what makes the background check
 * behave. The 6h timer re-emits `available` for the same version indefinitely,
 * and a flag would let it re-nag on every tick; a *new* version or a different
 * error produces a new key and correctly shows again.
 */
function dismissKey(state: UpdateState): string {
  switch (state.status) {
    case "available":
      return `available:${state.version}`;
    case "error":
      return `error:${state.message ?? ""}`;
    case "none":
      return "none";
    default:
      return "";
  }
}

export interface Updater extends UpdateState {
  /** False in the browser build — callers should render nothing. */
  supported: boolean;
  /** True when the user asked for this check, so "you're up to date" can show. */
  interactive: boolean;
  dismissed: boolean;
  check: () => void;
  download: () => void;
  install: () => void;
  dismiss: () => void;
}

export function useUpdater(): Updater {
  const [state, setState] = useState<UpdateState>(INITIAL);
  const [supported, setSupported] = useState(false);
  const [interactive, setInteractive] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    const api = bridge();
    if (!api) return;
    setSupported(true);
    // The launch check fires 30s in and may well beat this mount. That costs a
    // delay rather than the prompt: the 6h timer re-emits `available` until
    // the update is taken.
    return api.onUpdate((event) => setState((prev) => reduce(prev, event)));
  }, []);

  const check = useCallback(() => {
    setInteractive(true);
    bridge()?.checkForUpdates();
  }, []);

  const download = useCallback(() => {
    // electron-updater emits nothing between the request and the first
    // progress tick, which on a slow connection is long enough for the click
    // to look dropped. Enter the downloading state optimistically.
    setState((prev) => ({
      ...prev,
      status: "downloading",
      percent: 0,
      transferred: 0,
      total: 0,
      message: null,
    }));
    bridge()?.downloadUpdate();
  }, []);

  const install = useCallback(() => bridge()?.installUpdate(), []);

  const key = dismissKey(state);
  const dismiss = useCallback(() => {
    setInteractive(false);
    setDismissed(key);
  }, [key]);

  return {
    ...state,
    supported,
    interactive,
    dismissed: key !== "" && key === dismissed,
    check,
    download,
    install,
    dismiss,
  };
}
