"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import UsageAreaChart from "@/components/charts/UsageAreaChart";
import TokenComposition from "@/components/usage/TokenComposition";
import Button from "@/components/ui/Button";
import { useUsage, type UsageRange } from "@/lib/api";
import { COUNTERS, compactTokens } from "@/lib/agentPalette";

const VIEWS: UsageRange[] = ["session", "week", "all"];
const VIEW_LABEL: Record<UsageRange, string> = { session: "SESSION", week: "WEEK", all: "ALL" };

function chartLabel(range: UsageRange, label: string): string {
  if (range === "session") return label.slice(11, 16) || label; // "HH:MM" from "YYYY-MM-DD HH:MM:SS"
  if (range === "week") return label.slice(5); // "MM-DD"
  return label; // "all": already "YYYY-wNN"
}

/**
 * ARGUS.USAGE (§14) — SESSION / WEEK / ALL segmented view of tokens spent by
 * Argus's own chat/planner/study-generate calls, wired to `GET /api/usage`.
 *
 * Deliberately distinct from AGENT.USAGE (`AgentUsage.tsx`), which is
 * account-wide spend across every local coding agent. The two are never summed
 * into one number: they answer different questions, and adding them would
 * double-count nothing while implying a total that means neither thing.
 *
 * Shares the composition bar and area chart with that panel so they read as
 * one family — including the fix that every token counter is now visible,
 * rather than an `in · out` line that never added up to the headline.
 *
 * There is deliberately no budget bar. §14 called for "config-less soft caps
 * client-side" and the first pass hardcoded 25k/175k/2M, which rendered as an
 * authoritative "% of soft cap" against a number nothing configures and no
 * plan ever set. A made-up limit is worse than no limit; this waits for a real
 * budget setting.
 */
export default function TokenUsage() {
  const [view, setView] = useState<UsageRange>("session");
  const { data, error, isLoading, mutate } = useUsage(view);

  const total = data?.total_tokens ?? 0;
  const features = data?.features ?? [];
  const maxFeature = Math.max(1, ...features.map((feature) => feature.total_tokens));

  return (
    <Panel
      label="ARGUS.USAGE"
      headerRight={
        <div className="flex border border-line font-mono text-micro uppercase tracking-[0.14em]">
          {VIEWS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setView(option)}
              aria-pressed={view === option}
              className={`border-l border-line px-1.5 py-1 first:border-l-0 transition-colors ${
                view === option
                  ? "bg-[var(--ac-bg)] text-[var(--ac)]"
                  : "text-ink-faint hover:text-ink-muted"
              }`}
            >
              {VIEW_LABEL[option]}
            </button>
          ))}
        </div>
      }
    >
      {error && !data ? (
        // Without this an unreachable backend rendered as "no usage recorded
        // yet", which claims a fact the panel does not actually have.
        <div className="border border-danger/50 px-4 py-6">
          <p className="font-mono text-label text-danger">could not load usage</p>
          <p className="mt-1 text-label leading-relaxed text-ink-faint">
            {error instanceof Error ? error.message : "the backend did not answer"}
          </p>
          <Button className="mt-3" onClick={() => void mutate()}>
            RETRY
          </Button>
        </div>
      ) : isLoading && total === 0 ? (
        <p className="text-label text-ink-faint">loading usage…</p>
      ) : total === 0 ? (
        <p className="text-label text-ink-faint">no usage recorded yet</p>
      ) : (
        <>
          <div className="flex flex-wrap items-baseline gap-2">
            <p
              className="font-mono text-title font-semibold tabular-nums text-ink-bright"
              title={`${total.toLocaleString()} tokens`}
            >
              {compactTokens(total)}
            </p>
            <span className="text-meta text-ink-faint">tokens</span>
            <span className="ml-auto font-mono text-label text-ink-muted">
              ≈${(data?.estimated_cost_usd ?? 0).toFixed(2)}
            </span>
          </div>

          {data && <TokenComposition counts={data} className="mt-3" />}

          <UsageAreaChart
            series={COUNTERS.map((counter) => ({
              key: counter.key,
              label: counter.label,
              color: "var(--ac)",
              opacity: counter.opacity,
              points: (data?.series ?? []).map((point) => point[counter.key] ?? 0),
            }))}
            labels={(data?.series ?? []).map((point) => chartLabel(view, point.label))}
            stacked
            className="mt-4 h-28"
          />

          {features.length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <p className="mb-2 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                by feature
              </p>
              <ul className="space-y-2">
                {features.map((feature) => (
                  <li key={feature.feature}>
                    <div className="flex items-baseline gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-meta uppercase tracking-wide text-ink-muted">
                        {feature.feature}
                      </span>
                      <span
                        className="shrink-0 font-mono text-meta tabular-nums text-ink"
                        title={`${feature.total_tokens.toLocaleString()} tokens`}
                      >
                        {compactTokens(feature.total_tokens)}
                      </span>
                    </div>
                    <div className="mt-1 h-1 w-full bg-sunken">
                      <div
                        className="h-1 bg-[var(--ac)]"
                        style={{ width: `${(feature.total_tokens / maxFeature) * 100}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data?.unpriced_models && data.unpriced_models.length > 0 && (
            <p className="mt-3 text-meta leading-relaxed text-ink-faint">
              cost excludes {data.unpriced_models.join(", ")} — Argus has no published rate for
              {data.unpriced_models.length === 1 ? " it" : " them"}.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
