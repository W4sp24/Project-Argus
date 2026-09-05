"use client";

import { useParams } from "next/navigation";
import LearnSession from "@/components/notebook/flashcards/LearnSession";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/learn — adaptive practice, feeding FSRS. */
export default function LearnPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck } = useDeck(Number.isFinite(deckId) ? deckId : null);

  return (
    <>
      <NotebookStatusLine title="Learn" />
      {deck ? (
        <LearnSession deck={deck} />
      ) : (
        <p className="text-body text-ink-faint">Loading deck…</p>
      )}
    </>
  );
}
