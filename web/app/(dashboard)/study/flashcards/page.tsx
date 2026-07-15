"use client";

import Flashcards from "@/components/preview/Flashcards";
import StudyStatusLine from "@/components/study/StudyStatusLine";
import StudyTabs from "@/components/study/StudyTabs";

export default function FlashcardsPage() {
  return (
    <>
      <StudyStatusLine title="Flashcards" />
      <StudyTabs />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Flashcards />
      </div>
    </>
  );
}
