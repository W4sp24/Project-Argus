"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";
import { useJournalSessions } from "@/lib/api";

const BAR_COLOR = "#8b5cf6"; // validated vs dark surface (dataviz six checks)
const DAYS_SHOWN = 14;

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

      <div className="grid gap-4 md:grid-cols-2">
        <GlassCard label="TASKS" title="Completion trend">
          <p className="text-sm text-ink-muted">
            A 14-day completion trend renders here once there&apos;s task history (Phase P4).
          </p>
        </GlassCard>
        <GlassCard label="STUDY" title="Study streak">
          <p className="text-sm text-ink-muted">
            Practice-exam scores and study streaks per course land here (Phase P4).
          </p>
        </GlassCard>
      </div>
    </>
  );
}
