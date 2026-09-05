"use client";

import { useParams } from "next/navigation";
import MatchGame from "@/components/notebook/flashcards/MatchGame";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/match — the timed pairing game. */
export default function MatchPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck } = useDeck(Number.isFinite(deckId) ? deckId : null);

  return (
    <>
      <NotebookStatusLine title="Match" />
      {deck ? <MatchGame deck={deck} /> : <p className="text-body text-ink-faint">Loading deck…</p>}
    </>
  );
}
