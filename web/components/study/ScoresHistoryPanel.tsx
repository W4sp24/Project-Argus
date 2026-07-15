"use client";

import MiniLineChart from "@/components/charts/MiniLineChart";
import Panel from "@/components/Panel";
import { useInsights } from "@/lib/api";

/**
 * SCORES.HISTORY (§4 Practice Exam page) — real data from
 * `GET /api/insights` (`study.courses[].attempts`, backend/insights.py
 * `_study()`). A single-SVG line chart per course with ≥2 attempts (§10: no
 * recharts outside /insights); one-line summaries otherwise.
 */
export default function ScoresHistoryPanel() {
  const { data: insights } = useInsights();
  const courses = insights?.study.courses ?? [];

  return (
    <Panel label="SCORES.HISTORY">
      {courses.length === 0 ? (
        <p className="text-[13px] text-ink-faint">No graded attempts yet.</p>
      ) : (
        <div className="space-y-4">
          {courses.map((course) => (
            <div key={course.course} className="border-b border-line pb-3 last:border-b-0 last:pb-0">
              <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                {course.course}
              </p>
              {course.attempts.length >= 2 ? (
                <MiniLineChart
                  values={course.attempts.map((a) => a.pct)}
                  labels={[course.attempts[0].date, course.attempts[course.attempts.length - 1].date]}
                />
              ) : (
                <ul className="space-y-1">
                  {course.attempts.map((attempt) => (
                    <li key={attempt.date} className="flex items-center justify-between font-mono text-[11px]">
                      <span className="text-ink-faint">{attempt.date}</span>
                      <span className="text-ink-muted">{attempt.pct}%</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
