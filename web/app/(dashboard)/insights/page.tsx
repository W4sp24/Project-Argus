"use client";

import dynamic from "next/dynamic";
import useSWR from "swr";
import Panel from "@/components/Panel";
import PageHeader from "@/components/PageHeader";
import ChartSkeleton from "@/components/charts/ChartSkeleton";
import { SERIES } from "@/components/charts/chartTheme";
import Heatmap from "@/components/dashboard/Heatmap";
import { fetcher, useJournalSessions } from "@/lib/api";

const BarChartPanel = dynamic(() => import("@/components/charts/BarChartPanel"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});
const StackedBarChartPanel = dynamic(() => import("@/components/charts/StackedBarChartPanel"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});
const LineChartPanel = dynamic(() => import("@/components/charts/LineChartPanel"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

const BAR_COLOR = SERIES.violet;
const DAYS_SHOWN = 14;

interface InsightsSummary {
  completion_trend: { date: string; completed: number }[];
  overdue: { date: string; count: number }[];
  calendar: { date: string; event_hours: number; focus_hours: number }[];
  study: { streak_days: number; courses: { course: string; attempts: { date: string; pct: number }[] }[] };
  configured: { gcal: boolean };
}

function shortDay(date: string): string {
  return date.slice(5).replace("-", "/");
}

function lastNDays(n: number): string[] {
  const days: string[] = [];
  const today = new Date();
  for (let i = n - 1; i >= 0; i -= 1) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    days.push(
      `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(
        day.getDate(),
      ).padStart(2, "0")}`,
    );
  }
  return days;
}

export default function InsightsPage() {
  const { data: sessions } = useJournalSessions();
  const { data: insights } = useSWR<InsightsSummary>("/api/insights", fetcher);

  const completion = (insights?.completion_trend ?? []).map((row) => ({
    day: shortDay(row.date),
    completed: row.completed,
  }));
  const overdue = (insights?.overdue ?? []).map((row) => ({
    day: shortDay(row.date),
    count: row.count,
  }));
  const calendar = (insights?.calendar ?? []).map((row) => ({
    day: shortDay(row.date),
    events: row.event_hours,
    focus: row.focus_hours,
  }));
  const courseColors = [SERIES.violet, SERIES.cyan, SERIES.rose];
  const scoreDays = Array.from(
    new Set(
      (insights?.study.courses ?? []).flatMap((course) =>
        course.attempts.map((attempt) => attempt.date),
      ),
    ),
  ).sort();
  const scores = scoreDays.map((date) => {
    const point: Record<string, string | number> = { day: shortDay(date) };
    for (const course of insights?.study.courses ?? []) {
      const attempt = course.attempts.filter((a) => a.date === date).at(-1);
      if (attempt) point[course.course] = attempt.pct;
    }
    return point;
  });

  const days = lastNDays(DAYS_SHOWN);
  const recent = (sessions ?? []).filter((session) => days.includes(session.date));
  const activity = days.map((date) => ({
    day: date.slice(5).replace("-", "/"),
    sessions: recent.filter((session) => session.date === date).length,
  }));
  const projectsTouched = new Set(recent.map((session) => session.project)).size;
  const filesChanged = recent.reduce((sum, session) => sum + session.files, 0);
  const lastWeek = days.slice(-7);
  const sessionsThisWeek = recent.filter((session) => lastWeek.includes(session.date)).length;

  return (
    <>
      <PageHeader
        label="INSIGHTS"
        title="How you're doing"
        subtitle="Trends from your tasks, calendar, study sessions — and your builds."
      />

      <div className="mb-6 grid gap-4">
        <Heatmap />
      </div>

      <section className="mb-6">
        <p className="eyebrow mb-3">{`// DEVELOPMENT ACTIVITY`}</p>
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <Panel title={`Coding sessions — last ${DAYS_SHOWN} days`}>
            <div className="h-56">
              <BarChartPanel data={activity} dataKey="sessions" color={BAR_COLOR} />
            </div>
          </Panel>

          <div className="grid grid-cols-3 gap-4 lg:grid-cols-1">
            {[
              { label: "SESSIONS · 7D", value: sessionsThisWeek },
              { label: "PROJECTS · 14D", value: projectsTouched },
              { label: "FILES CHANGED · 14D", value: filesChanged },
            ].map((stat) => (
              <Panel key={stat.label} className="flex flex-col justify-center">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                  {stat.label}
                </p>
                <p className="mt-1 font-display text-3xl font-semibold text-ink">{stat.value}</p>
              </Panel>
            ))}
          </div>
        </div>
      </section>

      <section className="mb-6">
        <p className="eyebrow mb-3">{`// LIFE METRICS`}</p>
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <Panel title="Tasks completed — last 14 days">
            <div className="h-56">
              <BarChartPanel data={completion} dataKey="completed" color={SERIES.violet} />
            </div>
          </Panel>

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-1">
            <Panel className="flex flex-col justify-center">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                STUDY STREAK
              </p>
              <p className="mt-1 font-display text-3xl font-semibold text-ink">
                {insights?.study.streak_days ?? "—"}
                <span className="ml-1 text-base font-normal text-ink-muted">days</span>
              </p>
            </Panel>
            <Panel className="flex flex-col justify-center">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                OVERDUE OPEN
              </p>
              <p className="mt-1 font-display text-3xl font-semibold text-ink">
                {insights ? insights.overdue.reduce((sum, row) => sum + row.count, 0) : "—"}
              </p>
            </Panel>
          </div>
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel label="CALENDAR" title="Calendar load vs focus time">
          {insights && !insights.configured.gcal ? (
            <p className="text-sm text-ink-muted">
              Connect Google Calendar (
              <span className="font-mono text-xs text-primary-soft">argus connect gcal</span>) to
              see meeting load against remaining focus hours.
            </p>
          ) : (
            <>
              <div className="mb-2 flex gap-4 font-mono text-[10px] uppercase tracking-[0.18em]">
                <span className="flex items-center gap-1.5 text-ink-muted">
                  <span className="h-2 w-2 rounded-sm" style={{ background: SERIES.violet }} />
                  events
                </span>
                <span className="flex items-center gap-1.5 text-ink-muted">
                  <span className="h-2 w-2 rounded-sm" style={{ background: SERIES.cyan }} />
                  focus
                </span>
              </div>
              <div className="h-48">
                <StackedBarChartPanel data={calendar} />
              </div>
            </>
          )}
        </Panel>

        <Panel label="OVERDUE" title="Overdue tasks by due date">
          {overdue.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing overdue — inbox zero energy.</p>
          ) : (
            <div className="h-48">
              <BarChartPanel data={overdue} dataKey="count" color={SERIES.rose} />
            </div>
          )}
        </Panel>

        <Panel label="STUDY" title="Practice-exam scores">
          {scores.length === 0 ? (
            <p className="text-sm text-ink-muted">
              Take a practice exam on the Study page and score trends per course appear here.
            </p>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap gap-4 font-mono text-[10px] uppercase tracking-[0.18em]">
                {(insights?.study.courses ?? []).map((course, i) => (
                  <span key={course.course} className="flex items-center gap-1.5 text-ink-muted">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: courseColors[i % courseColors.length] }}
                    />
                    {course.course}
                  </span>
                ))}
              </div>
              <div className="h-48">
                <LineChartPanel
                  data={scores}
                  series={(insights?.study.courses ?? []).map((course, i) => ({
                    key: course.course,
                    color: courseColors[i % courseColors.length],
                  }))}
                />
              </div>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
