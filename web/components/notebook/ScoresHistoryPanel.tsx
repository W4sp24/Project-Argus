"use client";

import MiniLineChart from "@/components/charts/MiniLineChart";
import NotebookPanel from "@/components/notebook/NotebookPanel";
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
    <NotebookPanel heading="Your scores" scale="body" pad="md">
      {courses.length === 0 ? (
        <p className="text-body text-nb-faint">No graded attempts yet.</p>
      ) : (
        <div className="flex flex-col gap-4">
          {courses.map((course) => {
            const first = course.attempts[0];
            const last = course.attempts[course.attempts.length - 1];
            return (
              <div
                key={course.course}
                className="border-b border-nb-line pb-4 last:border-b-0 last:pb-0"
              >
                <p className="mb-1.5 text-label text-nb-faint">
                  {course.course} · {course.attempts.length} attempt
                  {course.attempts.length === 1 ? "" : "s"}
                </p>
                {course.attempts.length >= 2 ? (
                  <>
                    <MiniLineChart
                      values={course.attempts.map((a) => a.pct)}
                      labels={[first.date, last.date]}
                    />
                    {/* The number the chart is drawn from, said plainly — a
                        sparkline shows a shape, not a figure. */}
                    <p className="mt-1.5 text-ctl text-nb-body">
                      {first.pct}% →{" "}
                      <b className={last.pct >= first.pct ? "text-ok" : "text-warn"}>{last.pct}%</b>
                    </p>
                  </>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {course.attempts.map((attempt) => (
                      <li
                        key={attempt.date}
                        className="flex items-center justify-between text-label"
                      >
                        <span className="text-nb-faint">{attempt.date}</span>
                        <span className="font-mono tabular-nums text-nb-body">{attempt.pct}%</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </NotebookPanel>
  );
}
