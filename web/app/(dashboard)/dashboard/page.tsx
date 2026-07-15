"use client";

import Link from "next/link";
import CliUsage from "@/components/CliUsage";
import StatRow from "@/components/StatRow";
import TokenUsage from "@/components/TokenUsage";
import ActivityFeed from "@/components/dashboard/ActivityFeed";
import BriefingCard from "@/components/dashboard/BriefingCard";
import Heatmap from "@/components/dashboard/Heatmap";
import IngestPanel from "@/components/dashboard/IngestPanel";
import InsightsChart from "@/components/dashboard/InsightsChart";
import PlannerTimeline from "@/components/dashboard/PlannerTimeline";
import TasksPanel from "@/components/dashboard/TasksPanel";
import { useDashboardStats } from "@/lib/useDashboardStats";
import { useTypewriter } from "@/lib/useTypewriter";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function greetingWord(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Burning the midnight oil";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export default function DashboardPage() {
  const stats = useDashboardStats();
  const { output: greeting, done: greetingDone } = useTypewriter(`${greetingWord()}, Ethan.`);

  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// SYS.GENERAL :: ${formatToday()} :: vault OK · index OK · agent idle`}</p>
        <h1 className="font-mono text-[23px] font-semibold tracking-tight text-ink-bright">
          {greeting}
          <span className={`text-[var(--ac)] ${greetingDone ? "animate-blink" : ""}`}>▊</span>
        </h1>
      </header>

      <div className="flex flex-col gap-4">
        <StatRow items={stats} />

        <Heatmap className="col-span-full" />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-4">
            <PlannerTimeline />
            <TasksPanel />
            <IngestPanel />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            {/* Chat moved to the ChatDrawer (TopBar CHAT / ⌘K), shared with /chat. */}
            <BriefingCard />
            <TokenUsage />
            <CliUsage />
            <ActivityFeed />
            <InsightsChart />
          </div>
        </div>

        <nav className="flex gap-5 border-t border-line pt-4 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint">
          <Link href="/journal" className="transition-colors hover:text-ink-bright">
            → JOURNAL
          </Link>
          <Link href="/review" className="transition-colors hover:text-ink-bright">
            → REVIEW
          </Link>
        </nav>
      </div>
    </>
  );
}
