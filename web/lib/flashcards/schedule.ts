/**
 * How a card's next review reads in the deck editor.
 *
 * Pure, and in `lib/` rather than in the component, so vitest can cover the
 * boundaries — "due now" vs "tomorrow" vs "next week" are exactly the cases
 * that read wrong if the arithmetic is off by a day, and they are invisible in
 * a screenshot.
 */

/**
 * Seconds a card takes to review. A flat constant, not a measurement: nothing
 * times a session, and the artboard's "at your pace" would be a claim we cannot
 * support. 15s is the figure the design's own arithmetic uses (42 cards ≈ 11
 * minutes).
 */
const SECONDS_PER_CARD = 15;

/** Never rounds to zero: "about 0 minutes of reviewing" reads as a bug. */
export function estimateMinutes(cards: number): number {
  return Math.max(1, Math.round((cards * SECONDS_PER_CARD) / 60));
}

export type NextReviewTone = "due" | "scheduled" | "muted";

export interface NextReview {
  label: string;
  tone: NextReviewTone;
}

/** Whole days between two instants, by calendar day rather than by 24h blocks:
 *  a card due at 23:00 tonight is "today", not "in 0.4 days". */
function daysUntil(due: Date, now: Date): number {
  const dueDay = Date.UTC(due.getFullYear(), due.getMonth(), due.getDate());
  const nowDay = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((dueDay - nowDay) / 86_400_000);
}

/**
 * @param dueAt   ISO timestamp of the next review, or null for a card that has
 *                never been reviewed — which is new, and due immediately.
 * @param suspended A suspended card never comes up; that outranks any date it
 *                  may still carry from before it was suspended.
 */
export function nextReview(
  dueAt: string | null,
  suspended: boolean,
  now: Date = new Date(),
): NextReview {
  if (suspended) return { label: "Suspended", tone: "muted" };
  if (!dueAt) return { label: "Due now", tone: "due" };

  const due = new Date(dueAt);
  if (Number.isNaN(due.getTime())) return { label: "—", tone: "muted" };
  if (due.getTime() <= now.getTime()) return { label: "Due now", tone: "due" };

  const days = daysUntil(due, now);
  if (days <= 0) return { label: "Later today", tone: "scheduled" };
  if (days === 1) return { label: "Tomorrow", tone: "scheduled" };
  // Inside the coming week a weekday name is the most readable form there is —
  // "Friday" needs no arithmetic from the reader. Past that it stops being
  // unambiguous (which Friday?), so it becomes a date.
  if (days < 7) {
    return { label: due.toLocaleDateString("en-US", { weekday: "long" }), tone: "scheduled" };
  }
  return {
    label: due.toLocaleDateString("en-US", { day: "numeric", month: "short" }),
    tone: "scheduled",
  };
}
