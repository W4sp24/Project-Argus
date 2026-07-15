"use client";

import { useState } from "react";
import MiniLineChart from "@/components/charts/MiniLineChart";
import Panel from "@/components/Panel";
import { useUsage, type UsageRange } from "@/lib/api";

const VIEWS: UsageRange[] = ["session", "week", "all"];
const VIEW_LABEL: Record<UsageRange, string> = { session: "SESSION", week: "WEEK", all: "ALL" };

// Soft caps (§14: "config-less soft caps client-side" — no budget config
// exists yet, so these mirror the pre-wiring mock's numbers): a single
// session's worth, a weekly budget, and a lifetime/monthly-ish budget.
const SOFT_CAPS: Record<UsageRange, number> = { session: 25_000, week: 175_000, all: 2_000_000 };

function chartLabel(range: UsageRange, label: string): string {
  if (range === "session") return label.slice(11, 16) || label; // "HH:MM" from "YYYY-MM-DD HH:MM:SS"
  if (range === "week") return label.slice(5); // "MM-DD"
  return label; // "all": already "YYYY-wNN"
}

/**
 * TOKENS.CLAUDE (§14) — SESSION / WEEK / ALL segmented view of Claude token
 * spend, wired to the real `GET /api/usage` (backend/usage.py). One SVG line
 * chart, no recharts (§10). Empty state until the token_usage table has rows.
 */
export default function TokenUsage() {
  const [view, setView] = useState<UsageRange>("session");
  const { data, isLoading } = useUsage(view);

  const hasData = (data?.total_tokens ?? 0) > 0;
  const pctOfCap = data ? Math.min(100, Math.round((data.total_tokens / SOFT_CAPS[view]) * 100)) : 0;

  return (
    <Panel
      label="TOKENS.CLAUDE"
      headerRight={
        <div className="flex border border-line font-mono text-[9px] uppercase tracking-[0.14em]">
          {VIEWS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setView(option)}
              aria-pressed={view === option}
              className={`border-l border-line px-1.5 py-1 first:border-l-0 transition-colors ${
                view === option ? "bg-[var(--ac-bg)] text-[var(--ac)]" : "text-ink-faint hover:text-ink-muted"
              }`}
            >
              {VIEW_LABEL[option]}
            </button>
          ))}
        </div>
      }
    >
      {isLoading && !data ? (
        <p className="text-[12.5px] text-ink-faint">loading usage…</p>
      ) : !hasData ? (
        <p className="text-[12.5px] text-ink-faint">no usage recorded yet</p>
      ) : (
        <>
          <p className="font-mono text-2xl font-semibold text-ink-bright">
            {data!.total_tokens.toLocaleString()}
            <span className="ml-1.5 text-xs font-normal text-ink-faint">tokens</span>
          </p>
          <p className="mt-1 font-mono text-[11px] text-ink-muted">
            in {data!.input_tokens.toLocaleString()} · out {data!.output_tokens.toLocaleString()} · ≈$
            {data!.estimated_cost_usd.toFixed(2)}
          </p>

          <div className="mt-2">
            <div className="h-1 w-full bg-sunken">
              <div className="h-1 bg-[var(--ac)]" style={{ width: `${pctOfCap}%` }} />
            </div>
            <p className="mt-1 font-mono text-[10px] text-ink-faint">{pctOfCap}% of soft cap</p>
          </div>

          <div className="mt-3">
            <MiniLineChart
              values={data!.series.map((point) => point.total_tokens)}
              labels={data!.series.map((point) => chartLabel(view, point.label))}
            />
          </div>

          <ul className="mt-3 space-y-1 border-t border-line pt-2">
            {data!.features.map((feature) => (
              <li key={feature.feature} className="flex items-center justify-between font-mono text-[11px]">
                <span className="uppercase tracking-wide text-ink-faint">{feature.feature}</span>
                <span className="text-ink-muted">{feature.total_tokens.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
