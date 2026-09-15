"use client";

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
          <ExamWorkspace />
        </div>
        <div className="min-w-0">
          <ScoresHistoryPanel />
        </div>
      </div>
    </>
  );
}
