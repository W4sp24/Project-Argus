"use client";

import { useToast } from "@/components/Toast";
import { useStandalone } from "@/lib/standalone";

/**
 * Named target, so a second click focuses the window that already exists
 * rather than opening another. The Electron shell tracks the same window and
 * focuses it for the same reason.
 */
export const NOTEBOOK_WINDOW = "argus-notebook";

/** The URL a popped-out Notebook window opens at. */
export const NOTEBOOK_STANDALONE_URL = "/notebook?window=standalone";

/**
 * Move the Notebook into a window of its own.
 *
 * One `window.open` serves three environments, which is why it is a plain web
 * API rather than an IPC call:
 *
 *   - In a browser it is a real second window.
 *   - In Electron it is intercepted by `setWindowOpenHandler`
 *     (desktop/main.js), which allows same-origin `/notebook` URLs and keeps
 *     denying everything else exactly as before.
 *   - Playwright addresses the result through `context.waitForEvent("page")`,
 *     so the behaviour is testable without driving Electron.
 */
export default function PopOutButton() {
  const { show } = useToast();
  const standalone = useStandalone();

  // The window that *is* the pop-out must not offer to pop itself out again.
  if (standalone !== null) return null;

  return (
    <button
      type="button"
      onClick={() => {
        const opened = window.open(NOTEBOOK_STANDALONE_URL, NOTEBOOK_WINDOW, "width=1280,height=880");
        if (opened === null) {
          // Only reachable in a browser, and only if pop-ups are blocked for
          // this origin -- the click is a user gesture, so nothing else should
          // stop it. Saying which setting is at fault beats a dead button.
          show("your browser blocked the window — allow pop-ups for Argus", { tone: "error" });
          return;
        }
        opened.focus();
      }}
      className="min-h-8 shrink-0 border border-line px-2 py-1 font-mono text-label uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
    >
      pop out ↗
    </button>
  );
}
