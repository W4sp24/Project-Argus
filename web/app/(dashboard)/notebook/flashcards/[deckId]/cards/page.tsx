"use client";

import { useParams } from "next/navigation";
import BrowseSession from "@/components/notebook/flashcards/BrowseSession";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/cards — browse and cram, no scheduling. */
export default function BrowsePage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck, mutate: refresh } = useDeck(Number.isFinite(deckId) ? deckId : null);

  return (
    <>
      <NotebookStatusLine title="Flashcards" />
      {deck ? (
        <BrowseSession deck={deck} onStarred={() => void refresh()} />
      ) : (
        <p className="text-body text-ink-faint">Loading deck…</p>
      )}
    </>
  );
}
