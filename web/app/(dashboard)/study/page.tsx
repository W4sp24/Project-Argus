"use client";

import Link from "next/link";
import CoursesPanel from "@/components/study/CoursesPanel";
import StudyStatusLine from "@/components/study/StudyStatusLine";
import StudyTabs from "@/components/study/StudyTabs";
import Panel from "@/components/Panel";
import StatRow, { type StatItem } from "@/components/StatRow";
import { useInsights, useStudyCourses, useStudyExams } from "@/lib/api";
import { useNextExam, useWeakTopics } from "@/lib/useStudySignals";

// "cards due" has no backing data source until real decks + SRS scheduling
// exist (§8 flags.flashcards: preview) — the spec calls this out explicitly
// as mock. Kept as a fixed small number rather than something that looks
// live but isn't.
const MOCK_CARDS_DUE: number = 7;

export default function StudyOverviewPage() {
  const { data: courses } = useStudyCourses();
  const { data: exams } = useStudyExams();
  const { data: insights } = useInsights();
  const nextExam = useNextExam();
  const weakTopics = useWeakTopics();

  const stats: StatItem[] = [
    { href: "/study", label: "courses", value: courses?.length ?? "–" },
    { href: "/study/exam", label: "next exam", value: nextExam ? `T-${nextExam.days}` : "—" },
    { href: "/study/flashcards", label: "cards due", value: MOCK_CARDS_DUE },
    { href: "/study", label: "streak", value: insights?.study.streak_days ?? "–", unit: "days" },
    { href: "/study", label: "weak topics", value: weakTopics.length },
  ];

  return (
    <>
      <StudyStatusLine title="Study" />
      <StudyTabs />

      <div className="flex flex-col gap-4">
        <StatRow items={stats} />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0">
            <CoursesPanel />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <Panel label="FLASHCARDS" preview>
              <p className="text-[13px] text-ink-muted">
                {MOCK_CARDS_DUE} card{MOCK_CARDS_DUE === 1 ? "" : "s"} due for review (mock).
              </p>
              <Link
                href="/study/flashcards"
                className="mt-3 inline-block font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--ac)] transition-opacity hover:opacity-80"
              >
                OPEN DECK →
              </Link>
            </Panel>

            <Panel label="PRACTICE.EXAM">
              <p className="text-[13px] text-ink-muted">
                {exams?.length ?? 0} generated exam{(exams?.length ?? 0) === 1 ? "" : "s"} ready to take.
              </p>
              <Link
                href="/study/exam"
                className="mt-3 inline-block font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--ac)] transition-opacity hover:opacity-80"
              >
                TAKE EXAM →
              </Link>
            </Panel>

            <Panel label="REVIEW.QUEUE">
              {weakTopics.length === 0 ? (
                <p className="text-[13px] text-ink-faint">
                  Nothing queued — missed exam questions land here after grading.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {weakTopics.slice(0, 8).map((topic) => (
                    <li
                      key={`${topic.course}-${topic.topic}`}
                      className="flex items-center gap-2 border-b border-line py-1.5 text-[12.5px] last:border-b-0"
                    >
                      <span className="shrink-0 font-mono text-[10px] uppercase text-ink-faint">{topic.course}</span>
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
