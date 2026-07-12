"use client";

import useSWR from "swr";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";
import { fetcher, useJournalSessions } from "@/lib/api";

// Palette validated vs dark glass surface #17092e (dataviz six checks pass).
const BAR_COLOR = "#8b5cf6";
const SERIES = { violet: "#8b5cf6", cyan: "#0891b2", rose: "#e11d48" };
const SURFACE = "#17092e";
const DAYS_SHOWN = 14;

interface InsightsSummary {
  completion_trend: { date: string; completed: number }[];
  overdue: { date: string; count: number }[];
  calendar: { date: string; event_hours: number; focus_hours: number }[];
  study: { streak_days: number; courses: { course: string; attempts: { date: string; pct: number }[] }[] };
  configured: { gcal: boolean };
}

const AXIS_TICK = { fill: "#6b5f94", fontSize: 10, fontFamily: "var(--font-mono)" };
const TOOLTIP_STYLE = {
  background: "rgba(23,9,46,0.95)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 12,
  fontSize: 12,
  color: "#ede9fe",
};

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

      <section className="mb-6">
        <p className="eyebrow mb-3">{`// DEVELOPMENT ACTIVITY`}</p>
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <GlassCard title={`Coding sessions — last ${DAYS_SHOWN} days`}>
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={activity} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={{ fill: "#6b5f94", fontSize: 10, fontFamily: "var(--font-mono)" }}
                    tickLine={false}
                    axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                    interval={1}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fill: "#6b5f94", fontSize: 10, fontFamily: "var(--font-mono)" }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    cursor={{ fill: "rgba(139,92,246,0.08)" }}
                    contentStyle={{
                      background: "rgba(23,9,46,0.95)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: 12,
                      fontSize: 12,
                      color: "#ede9fe",
                    }}
                    labelStyle={{ color: "#9d8fc7", fontFamily: "var(--font-mono)" }}
                  />
                  <Bar
                    dataKey="sessions"
                    fill={BAR_COLOR}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={18}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </GlassCard>

          <div className="grid grid-cols-3 gap-4 lg:grid-cols-1">
            {[
              { label: "SESSIONS · 7D", value: sessionsThisWeek },
              { label: "PROJECTS · 14D", value: projectsTouched },
              { label: "FILES CHANGED · 14D", value: filesChanged },
            ].map((stat) => (
              <GlassCard key={stat.label} className="flex flex-col justify-center">
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                  {stat.label}
                </p>
                <p className="mt-1 font-display text-3xl font-semibold text-ink">{stat.value}</p>
              </GlassCard>
            ))}
          </div>
        </div>
      </section>

      <section className="mb-6">
        <p className="eyebrow mb-3">{`// LIFE METRICS`}</p>
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <GlassCard title="Tasks completed — last 14 days">
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={completion} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
                    interval={1}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip cursor={{ fill: "rgba(139,92,246,0.08)" }} contentStyle={TOOLTIP_STYLE} />
                  <Bar
                    dataKey="completed"
                    fill={SERIES.violet}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={18}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </GlassCard>

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-1">
            <GlassCard className="flex flex-col justify-center">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                STUDY STREAK
              </p>
              <p className="mt-1 font-display text-3xl font-semibold text-ink">
                {insights?.study.streak_days ?? "—"}
                <span className="ml-1 text-base font-normal text-ink-muted">days</span>
              </p>
            </GlassCard>
            <GlassCard className="flex flex-col justify-center">
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                OVERDUE OPEN
              </p>
              <p className="mt-1 font-display text-3xl font-semibold text-ink">
                {insights ? insights.overdue.reduce((sum, row) => sum + row.count, 0) : "—"}
              </p>
            </GlassCard>
          </div>
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard label="CALENDAR" title="Calendar load vs focus time">
          {insights && !insights.configured.gcal ? (
            <p className="text-sm text-ink-muted">
              Connect Google Calendar (
              <span className="font-mono text-xs text-primary-soft">friday connect gcal</span>) to
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
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={calendar} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                    <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={false} />
                    <YAxis unit="h" tick={AXIS_TICK} tickLine={false} axisLine={false} />
                    <Tooltip
                      cursor={{ fill: "rgba(139,92,246,0.08)" }}
                      contentStyle={TOOLTIP_STYLE}
                    />
                    <Bar
                      dataKey="events"
                      stackId="day"
                      fill={SERIES.violet}
                      stroke={SURFACE}
                      strokeWidth={2}
                      maxBarSize={22}
                    />
                    <Bar
                      dataKey="focus"
                      stackId="day"
                      fill={SERIES.cyan}
                      stroke={SURFACE}
                      strokeWidth={2}
                      radius={[4, 4, 0, 0]}
                      maxBarSize={22}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </GlassCard>

        <GlassCard label="OVERDUE" title="Overdue tasks by due date">
          {overdue.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing overdue — inbox zero energy.</p>
          ) : (
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={overdue} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={false} />
                  <YAxis
                    allowDecimals={false}
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip cursor={{ fill: "rgba(225,29,72,0.08)" }} contentStyle={TOOLTIP_STYLE} />
                  <Bar dataKey="count" fill={SERIES.rose} radius={[4, 4, 0, 0]} maxBarSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </GlassCard>

        <GlassCard label="STUDY" title="Practice-exam scores">
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
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={scores} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                    <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={false} />
                    <YAxis domain={[0, 100]} unit="%" tick={AXIS_TICK} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    {(insights?.study.courses ?? []).map((course, i) => (
                      <Line
                        key={course.course}
                        dataKey={course.course}
                        stroke={courseColors[i % courseColors.length]}
                        strokeWidth={2}
                        dot={{ r: 4, strokeWidth: 0, fill: courseColors[i % courseColors.length] }}
                        connectNulls
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </GlassCard>
      </div>
    </>
  );
}
