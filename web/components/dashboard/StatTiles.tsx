"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher, useInsights } from "@/lib/api";

interface AgendaLite {
  tasks: { due: string | null; scheduled: string | null }[];
}

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function Tile({ href, label, value, unit }: { href: string; label: string; value: string | number; unit?: string }) {
  return (
    <Link
      href={href}
      className="glass glass-hover flex min-w-0 flex-col gap-1 px-4 py-3"
      prefetch={true}
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        {label}
      </span>
      <span className="font-display text-2xl font-semibold text-ink">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
      </span>
    </Link>
  );
}

export default function StatTiles() {
  const { data: insights } = useInsights();
  const { data: agenda } = useSWR<AgendaLite>("/api/agenda", fetcher);

  const today = localToday();
  const anchor = (task: { due: string | null; scheduled: string | null }) =>
    task.due ?? task.scheduled;
  const dueToday = agenda ? agenda.tasks.filter((task) => anchor(task) === today).length : "–";
  const overdue = agenda
    ? agenda.tasks.filter((task) => {
        const date = anchor(task);
        return date !== null && date < today;
      }).length
    : "–";
  const doneToday =
    insights?.completion_trend.find((day) => day.date === today)?.completed ?? "–";
  const streak = insights?.study.streak_days ?? "–";
  const focus = insights?.calendar[insights.calendar.length - 1]?.focus_hours ?? "–";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <Tile href="/tasks" label="due today" value={dueToday} />
      <Tile href="/tasks" label="overdue" value={overdue} />
      <Tile href="/insights" label="done today" value={doneToday} />
      <Tile href="/study" label="streak" value={streak} unit="days" />
      <Tile href="/insights" label="focus" value={focus} unit="h" />
    </div>
  );
}
