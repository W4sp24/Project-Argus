"use client";

import { useParams } from "next/navigation";
import ReviewSession from "@/components/notebook/flashcards/ReviewSession";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/review — the FSRS session. */
export default function ReviewPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck } = useDeck(Number.isFinite(deckId) ? deckId : null);

  return (
    <>
      <NotebookStatusLine title="Review" />
      {deck ? (
        <ReviewSession deckId={deck.id} deckTitle={deck.title} />
      ) : (
        <p className="text-body text-ink-faint">Loading deck…</p>
      )}
    </>
  );
}
