"use client";

import Link from "next/link";
import CoursesPanel from "@/components/notebook/CoursesPanel";
import NotebookIngestCard from "@/components/notebook/NotebookIngestCard";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import NotebookTabs from "@/components/notebook/NotebookTabs";
import TodayPanel, { estimateMinutes } from "@/components/notebook/TodayPanel";
import { useDueSummary, useStudyCourses } from "@/lib/api";
import { useWeakTopics } from "@/lib/useStudySignals";

export default function NotebookOverviewPage() {
  const { mutate: refreshCourses } = useStudyCourses();
  const { data: dueSummary } = useDueSummary();
  const weakTopics = useWeakTopics();

  const dueDecks = (dueSummary?.decks ?? []).filter((deck) => deck.due > 0).slice(0, 5);

  return (
    <>
      <NotebookStatusLine title="Notebook" />
      <NotebookTabs />

      <div className="grid gap-5 lg:grid-cols-shell">
        <div className="flex min-w-0 flex-col gap-5">
          <TodayPanel />
          <CoursesPanel />
        </div>

        <div className="flex min-w-0 flex-col gap-5">
          <NotebookPanel heading="Due now" scale="body" pad="md">
            {dueDecks.length === 0 ? (
              <p className="text-body text-nb-faint">
                Nothing is due. Decks you review appear here when they come round.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {dueDecks.map((deck, index) => (
                  <Link
                    key={deck.deck_id}
                    href={`/notebook/flashcards/${deck.deck_id}/review`}
                    className={`block rounded-tile border p-3 transition-colors ${
                      // The busiest deck is the one "Start reviewing" opens, so
                      // it is marked here too rather than leaving two different
                      // answers to "where do I begin".
                      index === 0
                        ? "border-[var(--ac)] bg-nb-acBg"
                        : "border-nb-line hover:border-nb-lineHi"
                    }`}
                  >
                    <p className="truncate text-body font-medium text-nb-ink">{deck.title}</p>
                    <p
                      className={`mt-0.5 text-label ${
                        index === 0 ? "text-[var(--ac)]" : "text-nb-body"
                      }`}
                    >
                      {deck.due} card{deck.due === 1 ? "" : "s"} · ~{estimateMinutes(deck.due)} min
                    </p>
                  </Link>
                ))}
              </div>
            )}
          </NotebookPanel>

          <NotebookPanel
            heading="Worth another look"
            subheading="From questions you missed on graded exams."
            scale="body"
            pad="md"
          >
            {weakTopics.length === 0 ? (
              <p className="text-body text-nb-faint">
                Nothing queued — missed exam questions land here after grading.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {weakTopics.slice(0, 8).map((topic) => (
                  <span
                    key={`${topic.course}-${topic.topic}`}
                    className="rounded-full border border-nb-line px-2.5 py-1 text-label text-nb-body"
                  >
                    {topic.topic}
                  </span>
                ))}
              </div>
            )}
          </NotebookPanel>

          <NotebookIngestCard onIngested={() => void refreshCourses()} />
        </div>
      </div>
    </>
  );
}
