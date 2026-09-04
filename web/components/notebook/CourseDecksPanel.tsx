"use client";

import Link from "next/link";
import Panel from "@/components/Panel";
import { useDueSummary, useFlashcardDecks } from "@/lib/api";

/** How many decks the panel shows before it offers the rest. */
const CAP = 6;

/** "15-Courses/CS201/materials/lecture-04.pdf" → "lecture-04.pdf". */
function filename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * A course's decks, in the course.
 *
 * They were already listed here, mixed in with study guides and practice exams
 * under one GENERATED heading capped at eight rows — so after a couple of
 * months of work a course's decks simply fell off the end of the only list that
 * named them. Worse, every deck row pointed at
 * `/notebook/flashcards?deck=<id>`, and nothing in the app has ever read a
 * `?deck=` parameter: the link landed on the library and left you to find the
 * deck by eye. The real route has always been `/notebook/flashcards/<id>`.
 *
 * Decks get their own panel because they are the only artifact here you come
 * back to daily. A guide is written once and read in Obsidian; an exam is sat
 * and scored. A deck has a due count that changes every day, which is the one
 * number worth putting in front of you when you open the course — and the
 * reason REVIEW is a link of its own rather than two clicks through the deck
 * page.
 *
 * `useFlashcardDecks(code)` is the same SWR key `CourseStudio` holds, so this
 * costs no extra request and refreshes when its generation lands.
 */
export default function CourseDecksPanel({ code }: { code: string }) {
  const { data: decks } = useFlashcardDecks(code);
  const { data: due } = useDueSummary();

  const dueFor = (deckId: number) =>
    due?.decks.find((entry) => entry.deck_id === deckId)?.due ?? 0;

  return (
    <Panel label={`DECKS · ${code}`}>
      {!decks ? (
        <p className="text-label text-ink-faint">Loading decks…</p>
      ) : decks.length === 0 ? (
        <p className="text-label text-ink-faint">
          No decks for {code} yet. STUDIO&apos;s{" "}
          <span className="font-mono text-meta uppercase">flashcard deck</span> writes one from
          the sources you have ticked.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {decks.slice(0, CAP).map((deck) => {
            const dueCount = dueFor(deck.id);
            return (
              <li key={deck.id} className="border border-line px-2.5 py-2">
                <div className="flex items-start gap-2">
                  <Link
                    href={`/notebook/flashcards/${deck.id}`}
                    className="min-w-0 flex-1 transition-colors hover:text-[var(--ac)]"
                  >
                    <span className="block truncate text-label text-ink">{deck.title}</span>
                    <span className="mt-0.5 block truncate font-mono text-micro text-ink-faint">
                      {deck.cards} card{deck.cards === 1 ? "" : "s"}
                      {deck.source_paths.length > 0
                        ? ` · from ${filename(deck.source_paths[0])}${
                            deck.source_paths.length > 1
                              ? ` +${deck.source_paths.length - 1}`
                              : ""
                          }`
                        : ""}
                    </span>
                  </Link>
                  {dueCount > 0 && (
                    <span className="shrink-0 border border-[var(--ac)] bg-[var(--ac-bg)] px-1 py-px font-mono text-micro text-[var(--ac)]">
                      {dueCount} due
                    </span>
                  )}
                </div>
                {deck.cards > 0 && (
                  <Link
                    href={`/notebook/flashcards/${deck.id}/review`}
                    className="mt-1 inline-block font-mono text-micro uppercase tracking-[0.14em] text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
                  >
                    review{dueCount > 0 ? ` ${dueCount}` : ""} →
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {(decks?.length ?? 0) > CAP && (
        <Link
          href="/notebook/flashcards"
          className="mt-2 inline-block font-mono text-meta text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
        >
          {CAP} of {decks?.length} · all decks
        </Link>
      )}
    </Panel>
  );
}
