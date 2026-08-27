"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import StatRow from "@/components/StatRow";
import TokenUsage from "@/components/TokenUsage";
import AutomationsHud from "@/components/dashboard/AutomationsHud";
import BriefingCard from "@/components/dashboard/BriefingCard";
import Heatmap from "@/components/dashboard/Heatmap";
import PlannerTimeline from "@/components/dashboard/PlannerTimeline";
import QuickLinks from "@/components/dashboard/QuickLinks";
import TasksPanel from "@/components/dashboard/TasksPanel";
import { useDashboardStats } from "@/lib/useDashboardStats";
import { useAutomationsStatus } from "@/lib/useAutomationsStatus";
import { useTypewriter } from "@/lib/useTypewriter";

/**
 * Below-the-fold panels, split out of the initial bundle.
 *
 * /dashboard mounts fourteen client components, which is why it was the one
 * route over the 135 kB first-load budget in `scripts/check-bundles.mjs` --
 * and had been for long enough that BUILD_STATE.md still recorded it as
 * passing at 113 kB. These five are the heaviest of the ones a reader has to
 * scroll to reach, so nothing above the fold waits on them.
 *
 * `ssr: false` matches the `Markdown.tsx` boundary: each fetches its own data
 * client-side through SWR and already renders an empty state while that is in
 * flight, so prerendering them buys nothing.
 *
 * AgentUsage is imported normally on /code and /system, which are inside
 * budget -- this boundary is the dashboard's, not the component's.
 */
const AutomationWidgets = dynamic(() => import("@/components/dashboard/AutomationWidgets"), {
  ssr: false,
});
const IngestPanel = dynamic(() => import("@/components/dashboard/IngestPanel"), { ssr: false });
const AgentUsage = dynamic(() => import("@/components/AgentUsage"), { ssr: false });
const ActivityFeed = dynamic(() => import("@/components/dashboard/ActivityFeed"), { ssr: false });
const InsightsChart = dynamic(() => import("@/components/dashboard/InsightsChart"), { ssr: false });

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
  // The ambient automations readout. Null until something is registered, so
  // an install without automations reads exactly as it did before.
  const automationsStatus = useAutomationsStatus();
  const health = automationsStatus
    ? `vault OK · index OK · ${automationsStatus}`
    : "vault OK · index OK · agent idle";

  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// SYS.GENERAL :: ${formatToday()} :: ${health}`}</p>
        <h1 className="font-mono text-display font-semibold tracking-tight text-ink-bright">
          {greeting}
          <span className={`text-[var(--ac)] ${greetingDone ? "animate-blink" : ""}`}>▊</span>
        </h1>
      </header>

      <div className="flex flex-col gap-4">
        <StatRow items={stats} />

        <Heatmap className="col-span-full" />

        <div className="grid gap-4 lg:grid-cols-shell">
          <div className="flex min-w-0 flex-col gap-4">
            <PlannerTimeline />
            <AutomationWidgets />
            <TasksPanel />
            <IngestPanel />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            {/* Chat moved to the ChatDrawer (TopBar CHAT / ⌘K), shared with /chat. */}
            <BriefingCard />
            <AutomationsHud />
            <QuickLinks />
            <TokenUsage />
            <AgentUsage />
            <ActivityFeed />
            <InsightsChart />
          </div>
        </div>

        <nav className="flex gap-5 border-t border-line pt-4 font-mono text-label uppercase tracking-[0.14em] text-ink-faint">
          <Link href="/journal" className="transition-colors hover:text-ink-bright">
            → JOURNAL
          </Link>
          <Link href="/review" className="transition-colors hover:text-ink-bright">
            → REVIEW
          </Link>
          <Link href="/automations" className="transition-colors hover:text-ink-bright">
            → AUTOMATIONS
          </Link>
        </nav>
      </div>
    </>
  );
}
