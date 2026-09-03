"use client";

import DeckList from "@/components/notebook/flashcards/DeckList";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import NotebookTabs from "@/components/notebook/NotebookTabs";

/**
 * /notebook/flashcards — the deck library.
 *
 * A deck is the noun; the four study activities are verbs applied to one, so
 * this page lists decks and the deck page launches sessions. It replaces a
 * screen that fused "generate a deck" with "study a deck" and could do neither
 * without a `flashcards.md` nothing in Argus ever wrote.
 */
export default function FlashcardsPage() {
  return (
    <>
      <NotebookStatusLine title="Flashcards" />
      <NotebookTabs />
      <DeckList />
    </>
  );
}
