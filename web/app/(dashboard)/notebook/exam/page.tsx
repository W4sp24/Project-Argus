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
      <div className="grid gap-4 lg:grid-cols-shell">
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
