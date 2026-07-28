"use client";

import AgentUsage from "@/components/AgentUsage";
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
 * vault + journal data; ACTIVE.WORK is still mock.
 *
 * The stat row carries no `commits`/`PRs open`: there is no GitHub connector,
 * so those were hardcoded 23 and 2, sitting in the same row as a real token
 * count and indistinguishable from it. A dashboard that invents two of its
 * five numbers cannot be trusted for the other three.
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
            { href: "/system", label: "tokens", value: tokens.toLocaleString() },
            { href: "/study", label: "streak", value: streak, unit: typeof streak === "number" ? "days" : undefined },
          ]}
        />

        <div className="grid gap-4 lg:grid-cols-shell">
          <div className="flex min-w-0 flex-col gap-4">
            <ProjectsVault />
            <DevJournalPanel />
            <ActiveWork />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <TokenUsage />
            <AgentUsage />
            <SessionsChart />
          </div>
        </div>
      </div>
    </>
  );
}
