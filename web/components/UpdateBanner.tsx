"use client";

import Button from "@/components/ui/Button";
import { useUpdater } from "@/lib/updates";

/** Whole MB is the right resolution here — the bar carries the fine detail. */
function mb(bytes: number): string {
  return `${Math.round(bytes / 1_000_000)} MB`;
}

/**
 * The desktop update prompt (bottom-right, above the page, under modals).
 *
 * Sits at `z-40` and mounts *before* ChatDrawer in the layout so the drawer —
 * also `z-40` — covers it rather than the other way round; the prompt is
 * persistent, so being behind a transient surface costs nothing.
 *
 * Downloads are never started for the user (see `desktop/lib/updater.js`): on
 * a metered connection even a differential update is tens of megabytes.
 */
export default function UpdateBanner() {
  const updater = useUpdater();
  const { status } = updater;

  if (!updater.supported || updater.dismissed) return null;
  if (status === "idle") return null;
  // "Up to date" and background failures are only worth a panel when the user
  // is the one who asked; otherwise the 6h timer would surface them unprompted.
  if ((status === "none" || status === "error") && !updater.interactive) return null;

  const dismissible = status === "available" || status === "none" || status === "error";

  return (
    <aside
      aria-label="Application update"
      className="animate-rise fixed bottom-4 right-4 z-40 w-[min(21rem,calc(100vw-2rem))] border border-line bg-panel p-4"
    >
      <div className="mb-2 flex items-center gap-2">
        <p className="eyebrow">▍UPDATE</p>
        {dismissible && (
          <Button
            variant="ghost"
            aria-label="Dismiss"
            className="ml-auto"
            onClick={updater.dismiss}
          >
            ×
          </Button>
        )}
      </div>

      {/* One live region for the whole body: the status line and the progress
          text both change in place, and announcing each separately would talk
          over itself on every progress tick. */}
      <div aria-live="polite">
        {status === "available" && (
          <>
            <p className="text-label text-ink">
              Argus <span className="font-mono text-ink-bright">{updater.version}</span> is
              available.
            </p>
            <div className="mt-3 flex items-center gap-2">
              <Button variant="primary" onClick={updater.download}>
                DOWNLOAD
              </Button>
              <Button variant="quiet" onClick={updater.dismiss}>
                LATER
              </Button>
            </div>
          </>
        )}

        {status === "downloading" && (
          <>
            <p className="text-label text-ink">Downloading update…</p>
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={updater.percent}
              className="mt-3 h-1 w-full bg-line"
            >
              <div
                className="h-full bg-[var(--ac)] transition-[width] duration-300"
                style={{ width: `${updater.percent}%` }}
              />
            </div>
            <p className="mt-2 font-mono text-meta tabular-nums text-ink-faint">
              {updater.percent}%
              {updater.total > 0 && ` · ${mb(updater.transferred)} / ${mb(updater.total)}`}
            </p>
          </>
        )}

        {status === "ready" && (
          <>
            <p className="text-label text-ink">
              Argus <span className="font-mono text-ink-bright">{updater.version}</span> is ready
              to install.
            </p>
            <p className="mt-1 text-meta text-ink-faint">
              Argus will close, install, and reopen. Unsaved work in other apps is unaffected.
            </p>
            <div className="mt-3">
              <Button variant="primary" onClick={updater.install}>
                RESTART &amp; INSTALL
              </Button>
            </div>
          </>
        )}

        {status === "none" && (
          <p className="text-label text-ink-faint">You&apos;re on the latest version.</p>
        )}

        {status === "error" && (
          <>
            <p className="text-label text-danger">Update failed.</p>
            <p className="mt-1 break-words font-mono text-meta text-ink-faint">
              {updater.message}
            </p>
            <div className="mt-3">
              <Button variant="quiet" onClick={updater.check}>
                TRY AGAIN
              </Button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
