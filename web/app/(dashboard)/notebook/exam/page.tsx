"use client";

import { Suspense } from "react";
import ExamWorkspace from "@/components/notebook/ExamWorkspace";
import ScoresHistoryPanel from "@/components/notebook/ScoresHistoryPanel";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import NotebookTabs from "@/components/notebook/NotebookTabs";

export default function PracticeExamPage() {
  return (
    <>
      <NotebookStatusLine title="Practice exam" />
      <NotebookTabs />
      {/* A narrower rail than `.shell`'s: the exam's own column carries long
          question text and lettered options, and the artboard gives it the
          extra width. 20rem = the artboard's 320px. */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0">
          {/* ExamWorkspace reads `?id=` so the Course Hub's per-exam links
              open the exam they name. `useSearchParams` opts a route out of
              static prerendering unless it sits under a Suspense boundary,
              and this page is otherwise fully static. */}
          <Suspense fallback={<p className="text-body text-nb-faint">Loading exams…</p>}>
            <ExamWorkspace />
          </Suspense>
        </div>
        <div className="min-w-0">
          <ScoresHistoryPanel />
        </div>
      </div>
    </>
  );
}
