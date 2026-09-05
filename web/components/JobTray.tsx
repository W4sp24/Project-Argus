"use client";

import { useEffect, useRef, useState } from "react";
import { labelFor, useJobs } from "@/lib/jobs";

/**
 * What is running right now, anywhere in the app.
 *
 * Renders nothing when nothing runs — this sits in the top bar and must not
 * cost a row of chrome to say "idle". The blinking bar is the same idiom
 * `IngestJobProgress` and `StudioAction` use, and for the same reason: it
 * moves only while something is actually running, so a stall looks like a
 * stall rather than like progress.
 *
 * This is the visible half of the fix for generation state living in a
 * component local: work started in a course hub stays legible after you leave
 * it, because the registry behind this tray is mounted above the router.
 */
export default function JobTray() {
  const { jobs } = useJobs();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Same dismissal contract as OverflowMenu in TopBar: Escape and an outside
  // click both close it. Not a Dialog — this must not trap focus or lock
  // scroll for a readout you glance at.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  // Closing on its own when the last job lands is deliberate: an empty popover
  // hanging open after the work finished reads as a stall.
  useEffect(() => {
    if (jobs.length === 0) setOpen(false);
  }, [jobs.length]);

  if (jobs.length === 0) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Background work: ${jobs.length} running`}
        className="flex min-h-8 shrink-0 items-center gap-1.5 border border-line px-2 py-1 font-mono text-label uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
      >
        <span className="h-1.5 w-1.5 animate-blink bg-[var(--ac)]" aria-hidden />
        {jobs.length}
      </button>

      {open && (
        <div
          className="animate-palette absolute right-0 top-full z-40 mt-1 w-72 border border-line bg-panel p-3"
          aria-label="Background work"
        >
          <ul className="space-y-3">
            {jobs.map((job) => (
              <li key={job.id}>
                <p className="font-mono text-label uppercase tracking-[0.12em] text-ink">
                  {labelFor(job)}
                </p>
                <p className="truncate text-label text-ink-faint" title={job.target}>
                  {job.target}
                </p>
                <div className="mt-1 h-0.5 w-full bg-line" aria-hidden>
                  <span className="block h-full w-1/3 animate-blink bg-[var(--ac)]" />
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-line pt-2 font-mono text-micro text-ink-faint">
            these keep running if you navigate away
          </p>
        </div>
      )}
    </div>
  );
}
