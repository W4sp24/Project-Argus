"use client";

import Link from "next/link";
import MiniLineChart from "@/components/charts/MiniLineChart";
import Panel from "@/components/Panel";
import { useInsights } from "@/lib/api";

function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** INSIGHTS.14D (§4 General, right rail) — last 14 days of completions, links to /insights. */
export default function InsightsChart() {
  const { data } = useInsights();
  const last14 = (data?.completion_trend ?? []).slice(-14);
  const total = last14.reduce((sum, day) => sum + day.completed, 0);

  return (
    <Panel
      label="INSIGHTS.14D"
      headerRight={
        <Link href="/insights" className="font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]">
          open →
        </Link>
      }
    >
      {last14.length === 0 ? (
        <p className="text-sm text-ink-faint">No completion data yet.</p>
      ) : (
        <>
          <p className="font-mono text-[11px] text-ink-muted">{total} tasks completed · last 14 days</p>
          <div className="mt-2">
            <MiniLineChart
              values={last14.map((day) => day.completed)}
              labels={[shortDate(last14[0].date), shortDate(last14[last14.length - 1].date)]}
            />
          </div>
        </>
      )}
    </Panel>
  );
}
