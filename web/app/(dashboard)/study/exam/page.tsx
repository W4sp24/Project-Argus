"use client";

import ExamWorkspace from "@/components/study/ExamWorkspace";
import ScoresHistoryPanel from "@/components/study/ScoresHistoryPanel";
import StudyStatusLine from "@/components/study/StudyStatusLine";
import StudyTabs from "@/components/study/StudyTabs";

export default function PracticeExamPage() {
  return (
    <>
      <StudyStatusLine title="Practice exam" />
      <StudyTabs />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
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
