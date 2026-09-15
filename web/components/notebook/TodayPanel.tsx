"use client";

import Link from "next/link";
import { useDueSummary, useFlashcardDecks, useMasteredHistory } from "@/lib/api";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import ProgressRing from "@/components/notebook/ProgressRing";
import { estimateMinutes } from "@/lib/flashcards/schedule";

/** Last seven days, mastered per day. Bars are decoration; the caption and the
 * per-bar title carry the numbers. */
function MasteredChart({ days }: { days: { date: string; mastered: number }[] }) {
  const peak = Math.max(1, ...days.map((day) => day.mastered));
  return (
    <div className="mt-5">
      <div className="flex items-end gap-1" aria-hidden="true">
        {days.map((day) => {
          const share = day.mastered / peak;
          return (
            <span
              key={day.date}
              title={`${day.date}: ${day.mastered}`}
              className={`flex-1 rounded-bar ${
                day.mastered === 0
                  ? "bg-nb-track"
                  : day.mastered === peak
                    ? "bg-[var(--ac)]"
                    : "bg-nb-bar"
              }`}
              style={{ height: `${8 + share * 40}px` }}
            />
          );
        })}
      </div>
      <p className="mt-2 text-label text-nb-faint">
        Last seven days · {days.reduce((sum, day) => sum + day.mastered, 0)} mastered
      </p>
    </div>
  );
}

/**
 * The Notebook's opening answer to "is it worth sitting down right now?"
 *
 * Deliberately *not* the design's daily-goal/level/badge hero: nothing in Argus
 * stores a goal, an XP curve or an award, and a ring counting up to an invented
 * target is a number that means nothing. The ring shows the real one — how much
 * of your material has reached mastery — and the chart shows real review work
 * per day. Both come from `flashcard_reviews`.
 */
export default function TodayPanel() {
  const { data: dueSummary } = useDueSummary();
  const { data: decks } = useFlashcardDecks();
  const { data: history } = useMasteredHistory(7);

  const due = dueSummary?.total ?? 0;
  const deckCount = dueSummary?.decks.filter((deck) => deck.due > 0).length ?? 0;
  const cards = decks?.reduce((sum, deck) => sum + deck.cards, 0) ?? 0;
  const mastered = decks?.reduce((sum, deck) => sum + deck.mastered, 0) ?? 0;

  // Start where the most work is, so the button is never a guess.
  const busiest = [...(dueSummary?.decks ?? [])].sort((a, b) => b.due - a.due)[0];

  return (
    <NotebookPanel>
      <div className="flex flex-wrap items-center gap-6">
        <ProgressRing value={mastered} total={cards} />
        <div className="min-w-0 flex-1">
          <h2 className="mb-1.5 font-body text-title font-semibold text-nb-ink">
            {due === 0
              ? "Nothing due — you are clear"
              : `${due} card${due === 1 ? "" : "s"} due${
                  deckCount > 1 ? ` across ${deckCount} decks` : ""
                }`}
          </h2>
          <p className="mb-4 text-body text-nb-body">
            {due === 0
              ? cards === 0
                ? "Generate a deck from a lecture, or write one by hand, and it will show up here."
                : "Everything you have written is scheduled. Come back when something comes round."
              : `About ${estimateMinutes(due)} minute${
                  estimateMinutes(due) === 1 ? "" : "s"
                } of reviewing.`}
          </p>
          {due > 0 && busiest && (
            <Link
              href={`/notebook/flashcards/${busiest.deck_id}/review`}
              className="inline-block rounded-ctl bg-[var(--ac)] px-5 py-2.5 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
            >
              Start reviewing
            </Link>
          )}
        </div>
      </div>
      {history && history.length > 0 && <MasteredChart days={history} />}
    </NotebookPanel>
  );
}
