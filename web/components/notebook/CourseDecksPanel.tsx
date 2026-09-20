"use client";

import Link from "next/link";
import NotebookPanel from "@/components/notebook/NotebookPanel";
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
    <NotebookPanel heading="Decks" scale="body" pad="md">
      {!decks ? (
        <p className="text-label text-nb-faint">Loading decks…</p>
      ) : decks.length === 0 ? (
        <p className="text-label text-nb-faint">
          No decks for {code} yet. &ldquo;Flashcard deck&rdquo; under Make something writes one
          from the sources you have ticked.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {decks.slice(0, CAP).map((deck) => {
            const dueCount = dueFor(deck.id);
            return (
              <li key={deck.id}>
                <Link
                  href={`/notebook/flashcards/${dueCount > 0 ? `${deck.id}/review` : deck.id}`}
                  className={`block rounded-tile border px-3 py-2.5 transition-colors ${
                    dueCount > 0
                      ? "border-[var(--ac)] bg-nb-acBg"
                      : "border-nb-line hover:border-nb-lineHi"
                  }`}
                >
                  <span className="block truncate text-ctl font-medium text-nb-ink">
                    {deck.title}
                  </span>
                  <span
                    className={`mt-0.5 block truncate text-meta ${
                      dueCount > 0 ? "text-[var(--ac)]" : "text-nb-faint"
                    }`}
                  >
                    {[
                      dueCount > 0 ? `${dueCount} due` : null,
                      deck.cards > 0 ? `${deck.mastered} of ${deck.cards} mastered` : "no cards",
                      deck.source_paths.length > 0
                        ? `from ${filename(deck.source_paths[0])}${
                            deck.source_paths.length > 1 ? ` +${deck.source_paths.length - 1}` : ""
                          }`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {(decks?.length ?? 0) > CAP && (
        <Link
          href="/notebook/flashcards"
          className="mt-2.5 inline-block text-label text-nb-body underline underline-offset-2 transition-colors hover:text-nb-ink"
        >
          {CAP} of {decks?.length} · all decks
        </Link>
      )}
    </NotebookPanel>
  );
}
