"use client";

import useSWR from "swr";
import { fetcher, useInsights, useUsage } from "@/lib/api";
import type { StatItem } from "@/components/StatRow";

interface AgendaLite {
  tasks: { due: string | null; scheduled: string | null }[];
}

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * General-mode stat row (§4, §9): due today / overdue / done today / streak /
 * tokens. Data computation kept verbatim from the original `StatTiles.tsx`
 * (useInsights + /api/agenda filtering) — only lifted up so the dashboard
 * page can also feed the same agenda payload to PLANNER.TIMELINE/TASKS.DUE.
 * `tokens` is the real session total from `GET /api/usage` (§14,
 * flags.tokenUsage: enabled).
 */
export function useDashboardStats(): StatItem[] {
  const { data: insights } = useInsights();
  const { data: agenda } = useSWR<AgendaLite>("/api/agenda", fetcher);
  const { data: usage } = useUsage("session");

  const today = localToday();
  const anchor = (task: { due: string | null; scheduled: string | null }) => task.due ?? task.scheduled;
  const dueToday = agenda ? agenda.tasks.filter((task) => anchor(task) === today).length : "–";
  const overdue = agenda
    ? agenda.tasks.filter((task) => {
        const date = anchor(task);
        return date !== null && date < today;
      }).length
    : "–";
  const doneToday = insights?.completion_trend.find((day) => day.date === today)?.completed ?? "–";
  const streak = insights?.study.streak_days ?? "–";
  const tokens = usage ? usage.total_tokens.toLocaleString() : "–";

  return [
    { href: "/tasks", label: "due today", value: dueToday },
    { href: "/tasks", label: "overdue", value: overdue },
    { href: "/insights", label: "done today", value: doneToday },
    { href: "/study", label: "streak", value: streak, unit: "days" },
    { href: "/system", label: "tokens", value: tokens },
  ];
}
