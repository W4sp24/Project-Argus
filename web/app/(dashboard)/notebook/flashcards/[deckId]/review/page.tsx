"use client";

import { useParams } from "next/navigation";
import ReviewSession from "@/components/notebook/flashcards/ReviewSession";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/review — the FSRS session. */
export default function ReviewPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck } = useDeck(Number.isFinite(deckId) ? deckId : null);

  return (
    <div className="mx-auto max-w-3xl">
      {deck ? (
        <ReviewSession deckId={deck.id} deckTitle={deck.title} />
      ) : (
        <p className="text-body text-nb-faint">Loading deck…</p>
      )}
    </div>
  );
}
