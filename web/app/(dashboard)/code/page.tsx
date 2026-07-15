"use client";

import ModeHeader from "@/components/ModeHeader";
import StatRow from "@/components/StatRow";
import TokenUsage from "@/components/TokenUsage";
import ActiveWork from "@/components/preview/ActiveWork";
import DevJournalPanel from "@/components/code/DevJournalPanel";
import ProjectsVault from "@/components/code/ProjectsVault";
import SessionsChart from "@/components/code/SessionsChart";
import { useInsights, useJournalSessions, useUsage } from "@/lib/api";

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

/**
 * Code mode (§4) — PROJECTS.VAULT / DEV.JOURNAL / SESSIONS.14D are real
 * vault + journal data; ACTIVE.WORK plus the `commits`/`PRs open` stats are
 * mock (no GitHub connector in this branch's ancestry).
 */
export default function CodePage() {
  const { data: sessions } = useJournalSessions();
  const { data: insights } = useInsights();
  const { data: usage } = useUsage("session");

  const weekStart = isoDaysAgo(6);
  const sessionsThisWeek = (sessions ?? []).filter((s) => s.date >= weekStart).length;
  const streak = insights?.study.streak_days ?? "–";
  const tokens = usage?.total_tokens ?? 0;

  return (
    <>
      <ModeHeader mode="code" greeting="Code workspace online." />

      <div className="flex flex-col gap-4">
        <StatRow
          items={[
            { href: "/code", label: "sessions/wk", value: sessions ? sessionsThisWeek : "–" },
            { href: "/code", label: "commits", value: 23 },
            { href: "/code", label: "prs open", value: 2 },
            { href: "/system", label: "tokens", value: tokens.toLocaleString() },
            { href: "/study", label: "streak", value: streak, unit: typeof streak === "number" ? "days" : undefined },
          ]}
        />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-4">
            <ProjectsVault />
            <DevJournalPanel />
            <ActiveWork />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <TokenUsage />
            <SessionsChart />
          </div>
        </div>
      </div>
    </>
  );
}
