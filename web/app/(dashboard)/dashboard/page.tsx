"use client";

import GlassCard from "@/components/GlassCard";
import BriefingCard from "@/components/dashboard/BriefingCard";
import CaptureCard from "@/components/dashboard/CaptureCard";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Burning the midnight oil";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export default function DashboardPage() {
  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// DASHBOARD · ${formatToday()}`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {greeting()}, <span className="gradient-text">Ethan</span>.
        </h1>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* Left column: the day */}
        <div className="flex min-w-0 flex-col gap-4">
          <BriefingCard />
          {/* Task 8 replaces this placeholder with <StatTiles /> */}
          <GlassCard label="STATS" title="Productivity">
            <p className="text-sm text-ink-faint">Stats coming online…</p>
          </GlassCard>
          {/* Task 10 replaces this placeholder with <AgendaCard /> */}
          <GlassCard label="AGENDA" title="Schedule">
            <p className="text-sm text-ink-faint">Agenda coming online…</p>
          </GlassCard>
          {/* Task 9 replaces this placeholder with <Heatmap /> */}
          <GlassCard label="ACTIVITY" title="A year at a glance">
            <p className="text-sm text-ink-faint">Heatmap coming online…</p>
          </GlassCard>
          <CaptureCard />
        </div>

        {/* Right rail: activity + chat dock */}
        <div className="flex min-w-0 flex-col gap-4">
          {/* Task 11 replaces this placeholder with <ActivityFeed /> */}
          <GlassCard label="RECENT" title="Latest activity">
            <p className="text-sm text-ink-faint">Feed coming online…</p>
          </GlassCard>
          {/* Task 12 replaces this placeholder with <ChatPanel variant="dock" /> */}
          <GlassCard label="CHAT" title="Ask Argus" className="lg:sticky lg:top-4">
            <p className="text-sm text-ink-faint">Mini chat coming online…</p>
          </GlassCard>
        </div>
      </div>
    </>
  );
}
