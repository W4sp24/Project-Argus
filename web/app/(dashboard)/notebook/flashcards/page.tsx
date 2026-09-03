"use client";

import Flashcards from "@/components/notebook/Flashcards";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import NotebookTabs from "@/components/notebook/NotebookTabs";

export default function FlashcardsPage() {
  return (
    <>
      <NotebookStatusLine title="Flashcards" />
      <NotebookTabs />
      <div className="grid gap-4 lg:grid-cols-shell">
        <Flashcards />
      </div>
    </>
  );
}
