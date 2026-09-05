"use client";

import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The frame every study activity sits in: where you are, how far through, and
 * the way out.
 *
 * Shared rather than repeated four times so that the exit route, the progress
 * readout and the keyboard-help line cannot drift between the four sessions —
 * the thing most likely to happen if each one owned its own header.
 */
export default function ActivityChrome({
  deckId,
  deckTitle,
  activity,
  progress,
  keys,
  children,
}: {
  deckId: number;
  deckTitle: string;
  activity: string;
  /** `n / N`, a score, a timer — whatever this activity counts. */
  progress?: ReactNode;
  /** Keyboard shortcuts, shown so they are discoverable rather than folklore. */
  keys?: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={`${activity} session`}>
      <header className="mb-4 flex flex-wrap items-center gap-3 border-b border-line pb-3">
        <Link
          href={`/notebook/flashcards/${deckId}`}
          className="font-mono text-label uppercase tracking-[0.12em] text-ink-faint transition-colors hover:text-ink"
        >
          ← {deckTitle}
        </Link>
        <span className="font-mono text-label uppercase tracking-[0.16em] text-[var(--ac)]">
          {activity}
        </span>
        {progress !== undefined && (
          <span className="ml-auto font-mono text-label tabular-nums text-ink-muted">
            {progress}
          </span>
        )}
      </header>

      {children}

      {keys && (
        <p className="mt-4 border-t border-line pt-2 font-mono text-micro text-ink-faint">
          {keys}
        </p>
      )}
    </section>
  );
}
