"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import UsageBlock from "@/components/UsageBlock";
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

  const pctOfCap = data
    ? Math.min(100, Math.round((data.total_tokens / SOFT_CAPS[view]) * 100))
    : 0;

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
      <UsageBlock
        isLoading={isLoading}
        totalTokens={data?.total_tokens ?? 0}
        inputTokens={data?.input_tokens ?? 0}
        outputTokens={data?.output_tokens ?? 0}
        estimatedCostUsd={data?.estimated_cost_usd ?? 0}
        pctOfCap={pctOfCap}
        series={(data?.series ?? []).map((point) => ({
          label: chartLabel(view, point.label),
          value: point.total_tokens,
        }))}
        rows={(data?.features ?? []).map((feature) => ({
          label: feature.feature,
          value: feature.total_tokens,
        }))}
      />
    </Panel>
  );
}
