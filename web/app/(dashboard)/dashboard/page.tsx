"use client";

import Panel from "@/components/Panel";
import ChatPanel from "@/components/chat/ChatPanel";
import ActivityFeed from "@/components/dashboard/ActivityFeed";
import AgendaCard from "@/components/dashboard/AgendaCard";
import BriefingCard from "@/components/dashboard/BriefingCard";
import CaptureCard from "@/components/dashboard/CaptureCard";
import Heatmap from "@/components/dashboard/Heatmap";
import StatTiles from "@/components/dashboard/StatTiles";

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
          {greeting()}, <span className="text-[var(--ac)]">Ethan</span>.
        </h1>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* Left column: the day */}
        <div className="flex min-w-0 flex-col gap-4">
          <BriefingCard />
          <StatTiles />
          <AgendaCard />
          <Heatmap />
          <CaptureCard />
        </div>

        {/* Right rail: activity + chat dock */}
        <div className="flex min-w-0 flex-col gap-4">
          <ActivityFeed />
          <Panel label="CHAT" title="Ask Argus" className="flex max-h-[32rem] min-h-[20rem] flex-col lg:sticky lg:top-4">
            <ChatPanel variant="dock" />
          </Panel>
        </div>
      </div>
    </>
  );
}
