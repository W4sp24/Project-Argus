"use client";

import Link from "next/link";
import type { ReactNode } from "react";

export type SegmentTone = "ok" | "warn" | "ac" | "track";

export interface Segment {
  /** Relative weight. A zero-weight segment renders nothing. */
  value: number;
  tone: SegmentTone;
}

const SEGMENT_CLASS: Record<SegmentTone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  ac: "bg-[var(--ac)]",
  track: "bg-nb-track",
};

/**
 * The frame every study activity sits in: where you are, how far through, and
 * the way out.
 *
 * Shared rather than repeated four times so that the exit route, the progress
 * readout and the keyboard-help line cannot drift between the four sessions —
 * the thing most likely to happen if each one owned its own header.
 *
 * The progress bar is segmented rather than a single fill because every session
 * has more than two states: in Review a card is banked, waiting to come round
 * again, or untouched, and collapsing those into one percentage throws away the
 * only part a person acts on.
 */
export default function ActivityChrome({
  deckId,
  deckTitle,
  activity,
  badge,
  progress,
  segments,
  caption,
  keys,
  children,
}: {
  deckId: number;
  deckTitle: string;
  /** Names the landmark. Not displayed — the deck name and the bar say where
   *  you are, and the route already said which activity you chose. */
  activity: string;
  /** A streak pill, a best time, a round number. */
  badge?: ReactNode;
  /** `n / N`, a score, a timer — whatever this activity counts. */
  progress?: ReactNode;
  segments?: Segment[];
  /** One quiet line under the bar, spelling the bar out in words. */
  caption?: string;
  /** Keyboard shortcuts, shown so they are discoverable rather than folklore. */
  keys?: string;
  children: ReactNode;
}) {
  const shown = (segments ?? []).filter((segment) => segment.value > 0);
  return (
    <section aria-label={`${activity} session`}>
      <header className="mb-3.5 flex flex-wrap items-center gap-3">
        <Link
          href={`/notebook/flashcards/${deckId}`}
          className="min-w-0 truncate text-ctl text-nb-body transition-colors hover:text-nb-ink"
        >
          ← {deckTitle}
        </Link>
        {badge && <span className="ml-auto shrink-0">{badge}</span>}
        {progress !== undefined && (
          <span
            className={`shrink-0 font-mono text-label font-semibold tabular-nums text-nb-ink ${
              badge ? "" : "ml-auto"
            }`}
          >
            {progress}
          </span>
        )}
      </header>

      {shown.length > 0 && (
        <div className="mb-1.5 flex h-1.5 gap-[3px]" aria-hidden="true">
          {shown.map((segment, index) => (
            <span
              key={index}
              className={`rounded-bar ${SEGMENT_CLASS[segment.tone]}`}
              style={{ flex: segment.value }}
            />
          ))}
        </div>
      )}
      {caption && <p className="mb-4 text-label text-nb-faint">{caption}</p>}

      {children}

      {keys && <p className="mt-3.5 font-mono text-label text-nb-faint">{keys}</p>}
    </section>
  );
}
