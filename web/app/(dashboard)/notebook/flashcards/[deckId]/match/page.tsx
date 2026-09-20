"use client";

import { useParams } from "next/navigation";
import MatchGame from "@/components/notebook/flashcards/MatchGame";
import { useDeck } from "@/lib/api";

/** /notebook/flashcards/[deckId]/match — the timed pairing game. */
export default function MatchPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck } = useDeck(Number.isFinite(deckId) ? deckId : null);

  // Full width, unlike the other three: Match is a grid of tiles you scan, and
  // a narrow column would stack them into a list you have to scroll.
  return (
    <>
      {deck ? <MatchGame deck={deck} /> : <p className="text-body text-nb-faint">Loading deck…</p>}
    </>
  );
}
