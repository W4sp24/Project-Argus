"use client";

import Link from "next/link";
import { useState } from "react";
import CoursesPanel from "@/components/notebook/CoursesPanel";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import NotebookTabs from "@/components/notebook/NotebookTabs";
import IngestPanel from "@/components/dashboard/IngestPanel";
import Panel from "@/components/Panel";
import StatRow, { type StatItem } from "@/components/StatRow";
import { useDueSummary, useInsights, useStudyCourses, useStudyExams } from "@/lib/api";
import { useNextExam, useWeakTopics } from "@/lib/useStudySignals";

export default function StudyOverviewPage() {
  const { data: courses, mutate: refreshCourses } = useStudyCourses();
  const { data: exams } = useStudyExams();
  const { data: insights } = useInsights();
  const { data: dueSummary } = useDueSummary();
  const nextExam = useNextExam();
  const weakTopics = useWeakTopics();
  const [ingestCourse, setIngestCourse] = useState("");

  // The upload target must be the course's real materials_path (from the
  // API), never a hand-built `<courses_dir>/<code>` — that was the reported
  // "ingesting files seems to not work" bug: a file uploaded to the course
  // *root* saved fine, but courses() only counts files under materials/, so
  // the row kept reading "0 materials" and GUIDE/EXAM stayed disabled.
  const ingestTargetCourse = courses?.find((course) => course.code === ingestCourse);
  const cardsDue = dueSummary?.total ?? 0;

  const stats: StatItem[] = [
    { href: "/notebook", label: "courses", value: courses?.length ?? "–" },
    { href: "/notebook/exam", label: "next exam", value: nextExam ? `T-${nextExam.days}` : "—" },
    { href: "/notebook/flashcards", label: "cards due", value: dueSummary ? cardsDue : "–" },
    { href: "/notebook", label: "streak", value: insights?.study.streak_days ?? "–", unit: "days" },
    { href: "/notebook", label: "weak topics", value: weakTopics.length },
  ];

  return (
    <>
      <NotebookStatusLine title="Notebook" />
      <NotebookTabs />

      <div className="flex flex-col gap-4">
        <StatRow items={stats} />

        <div className="grid gap-4 lg:grid-cols-shell">
          <div className="flex min-w-0 flex-col gap-4">
            <CoursesPanel />

            <div>
              {(courses?.length ?? 0) > 0 && (
                <div className="mb-2 flex items-center justify-end gap-2">
                  <label htmlFor="ingest-course" className="font-mono text-meta uppercase tracking-[0.1em] text-ink-faint">
                    upload target
                  </label>
                  <select
                    id="ingest-course"
                    value={ingestCourse}
                    onChange={(event) => setIngestCourse(event.target.value)}
                    className="border border-line bg-sunken px-2 py-1 font-mono text-meta uppercase tracking-[0.1em] text-ink-muted focus:border-lineHi focus:outline-none"
                  >
                    <option value="">00-Inbox/files (no course)</option>
                    {(courses ?? []).map((course) => (
                      <option key={course.code} value={course.code}>
                        {course.code}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <IngestPanel
                target={ingestTargetCourse?.materials_path}
                onUploaded={() => void refreshCourses()}
              />
            </div>
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <Panel label="FLASHCARDS">
              <p className="text-body text-ink-muted">
                {cardsDue} card{cardsDue === 1 ? "" : "s"} due for review.
              </p>
              <Link
                href="/notebook/flashcards"
                className="mt-3 inline-block font-mono text-label uppercase tracking-[0.14em] text-[var(--ac)] transition-opacity hover:opacity-80"
              >
                OPEN DECK →
              </Link>
            </Panel>

            <Panel label="PRACTICE.EXAM">
              <p className="text-body text-ink-muted">
                {exams?.length ?? 0} generated exam{(exams?.length ?? 0) === 1 ? "" : "s"} ready to take.
              </p>
              <Link
                href="/notebook/exam"
                className="mt-3 inline-block font-mono text-label uppercase tracking-[0.14em] text-[var(--ac)] transition-opacity hover:opacity-80"
              >
                TAKE EXAM →
              </Link>
            </Panel>

            <Panel label="REVIEW.QUEUE">
              {weakTopics.length === 0 ? (
                <p className="text-body text-ink-faint">
                  Nothing queued — missed exam questions land here after grading.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {weakTopics.slice(0, 8).map((topic) => (
                    <li
                      key={`${topic.course}-${topic.topic}`}
                      className="flex items-center gap-2 border-b border-line py-1.5 text-label last:border-b-0"
                    >
                      <span className="shrink-0 font-mono text-meta uppercase text-ink-faint">{topic.course}</span>
                      <span className="min-w-0 truncate text-ink-muted">{topic.topic}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </div>
      </div>
    </>
  );
}
