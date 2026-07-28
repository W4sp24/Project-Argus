"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import UsageAreaChart, { type ChartSeries } from "@/components/charts/UsageAreaChart";
import AddAgentDialog from "@/components/system/AddAgentDialog";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import AgentSelect, { type AgentOption } from "@/components/usage/AgentSelect";
import TokenComposition from "@/components/usage/TokenComposition";
import {
  deleteCustomAgent,
  useAgentUsage,
  type AgentUsage as AgentUsageSlice,
  type CliUsageRange,
} from "@/lib/api";
import { COUNTERS, agentColor, compactTokens } from "@/lib/agentPalette";

const RANGES: CliUsageRange[] = ["today", "week", "all"];
const RANGE_LABEL: Record<CliUsageRange, string> = { today: "TODAY", week: "WEEK", all: "ALL" };
const PREVIOUS_LABEL: Record<CliUsageRange, string> = {
  today: "yesterday",
  week: "prev week",
  all: "",
};

const COMBINED_ID = "all";

function chartLabel(range: CliUsageRange, label: string): string {
  if (range === "week") return label.slice(5); // "MM-DD" from "YYYY-MM-DD"
  return label; // today: already "HH:00"; all: already "YYYY-wNN"
}

/** `+12%` / `−4%` against the equivalent window before this one. */
function Delta({ current, previous }: { current: number; previous: number | null }) {
  if (previous === null || previous === 0) return null;
  const change = Math.round(((current - previous) / previous) * 100);
  if (change === 0) return null;
  const up = change > 0;
  return (
    <span
      className={`border px-1.5 py-px font-mono text-micro tabular-nums tracking-[0.1em] ${
        up ? "border-danger/60 text-danger" : "border-ok/60 text-ok"
      }`}
      title={`${previous.toLocaleString()} tokens in the previous period`}
    >
      {up ? "↑" : "↓"} {Math.abs(change)}%
    </span>
  );
}

/** Per-model rows as proportional bars — a plain right-aligned number hid the shape. */
function ModelBreakdown({ agent, tint }: { agent: AgentUsageSlice; tint: string }) {
  const top = agent.models.slice(0, 6);
  const max = Math.max(1, ...top.map((model) => model.total_tokens));
  if (top.length === 0) return null;

  return (
    <div>
      <p className="mb-2 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
        by model
      </p>
      <ul className="space-y-2">
        {top.map((model) => (
          <li key={model.model}>
            <div className="flex items-baseline gap-2">
              <span
                className="min-w-0 flex-1 truncate font-mono text-meta text-ink-muted"
                title={model.model}
              >
                {model.model}
              </span>
              <span
                className="shrink-0 font-mono text-meta tabular-nums text-ink"
                title={`${model.total_tokens.toLocaleString()} tokens`}
              >
                {compactTokens(model.total_tokens)}
              </span>
              <span className="w-14 shrink-0 text-right font-mono text-micro tabular-nums text-ink-faint">
                {model.unpriced ? "—" : `$${model.estimated_cost_usd.toFixed(2)}`}
              </span>
            </div>
            <div className="mt-1 h-1 w-full bg-sunken">
              <div
                className="h-1"
                style={{ width: `${(model.total_tokens / max) * 100}%`, background: tint }}
              />
            </div>
          </li>
        ))}
      </ul>
      {agent.models.length > top.length && (
        <p className="mt-2 font-mono text-micro text-ink-faint">
          + {agent.models.length - top.length} more
        </p>
      )}
    </div>
  );
}

/**
 * AGENT.USAGE — token spend across every local coding agent Argus can read,
 * wired to `GET /api/usage/agents`.
 *
 * The selector is a dropdown rather than a chip rail because the agent list is
 * open-ended now: three built-ins plus whatever the user registers with
 * `+ ADD AGENT`. Agents that are not installed stay in the list, dimmed and
 * inert, with their reason on the row — a selector that silently drops an
 * option reads as a bug rather than as information.
 *
 * `ALL AGENTS` overlays every agent in its own hue instead of stacking token
 * kinds, so the combined view answers "which tool is spending this" rather
 * than repeating the single-agent breakdown at a larger number.
 *
 * Distinct from ARGUS.USAGE (`TokenUsage.tsx`), which is only what Argus's own
 * chat/planner/study-generate calls spent. The two are never summed — they
 * answer different questions.
 */
export default function AgentUsage({ size = "default" }: { size?: "default" | "large" }) {
  const [range, setRange] = useState<CliUsageRange>("today");
  const [agentId, setAgentId] = useState<string>(COMBINED_ID);
  const [adding, setAdding] = useState(false);
  const { data, error, isLoading, mutate } = useAgentUsage(range);
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const agents = data?.agents ?? [];
  const active: AgentUsageSlice | undefined =
    agentId === COMBINED_ID ? data?.combined : agents.find((agent) => agent.id === agentId);

  const showingAll = agentId === COMBINED_ID;
  const tint = showingAll ? "var(--ac)" : agentColor(agentId);
  const wide = size === "large";

  const options: AgentOption[] = [
    {
      id: COMBINED_ID,
      label: "All agents",
      detected: data?.combined.detected ?? false,
      total: data?.combined.total_tokens ?? 0,
      hint: data?.combined.install_hint ?? "",
      trend: (data?.combined.series ?? []).map((point) => point.total_tokens),
    },
    ...agents.map((agent) => ({
      id: agent.id,
      label: agent.label,
      detected: agent.detected,
      total: agent.total_tokens,
      hint: agent.install_hint,
      trend: agent.series.map((point) => point.total_tokens),
    })),
  ];

  // The combined view compares agents; a single agent breaks down its own
  // token kinds. Same chart, two questions.
  //
  // Gated on spend, not on detection: rows outlive an uninstall by design (a
  // missing root is usually an unreachable folder, so `_reap` keeps them), and
  // they stay in the combined headline. Hiding their series only produced a
  // headline of N tokens above "no activity in this range".
  const charted = agents.filter((agent) => agent.total_tokens > 0);

  // Backend series are *sparse* — only buckets that actually have rows — so
  // agents that ran at different times have different-length series with
  // different labels. Overlaying them index-by-index drew one agent's 13:00
  // spend at another's 09:00. Project every agent onto the union of buckets.
  const buckets = showingAll
    ? Array.from(new Set(charted.flatMap((agent) => agent.series.map((point) => point.label))))
        .sort()
    : (active?.series ?? []).map((point) => point.label);

  const chartLabels = buckets.map((label) => chartLabel(range, label));

  const chartSeries: ChartSeries[] = showingAll
    ? charted.map((agent) => {
        const byBucket = new Map(agent.series.map((point) => [point.label, point.total_tokens]));
        return {
          key: agent.id,
          label: agent.label,
          color: agentColor(agent.id),
          points: buckets.map((label) => byBucket.get(label) ?? 0),
        };
      })
    : COUNTERS.map((counter) => ({
        key: counter.key,
        label: counter.label,
        color: tint,
        opacity: counter.opacity,
        points: (active?.series ?? []).map((point) => point[counter.key] ?? 0),
      }));

  async function removeAgent(agent: AgentUsageSlice) {
    const answer = await confirm({
      label: `Stop tracking ${agent.label}`,
      message: `Stop tracking "${agent.label}"?`,
      detail: "Its recorded usage is deleted too, so the combined total drops by its share.",
      confirmLabel: "STOP TRACKING",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteCustomAgent(agent.id);
      show(`agent :: ${agent.label} removed`);
      setAgentId(COMBINED_ID);
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not remove that agent");
    }
  }

  return (
    <>
      <Panel
        label="AGENT.USAGE"
        className={wide ? "min-h-[22rem]" : undefined}
        headerRight={
          <div className="flex items-center gap-2">
            <Button variant="quiet" onClick={() => setAdding(true)}>
              + ADD AGENT
            </Button>
            <div className="flex border border-line font-mono text-micro uppercase tracking-[0.14em]">
              {RANGES.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setRange(option)}
                  aria-pressed={range === option}
                  className={`border-l border-line px-1.5 py-1 first:border-l-0 transition-colors ${
                    range === option
                      ? "bg-[var(--ac-bg)] text-[var(--ac)]"
                      : "text-ink-faint hover:text-ink-muted"
                  }`}
                >
                  {RANGE_LABEL[option]}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="-mt-1 mb-4 flex flex-wrap items-center gap-2">
          <AgentSelect options={options} value={agentId} onChange={setAgentId} />
          {active && !active.builtin && (
            <Button variant="ghost" onClick={() => void removeAgent(active)}>
              STOP TRACKING
            </Button>
          )}
        </div>

        {error && !data ? (
          // An unreachable backend used to fall through to "no usage data",
          // which reads as "you have spent nothing" — the opposite of the truth.
          <div className="border border-danger/50 px-4 py-6">
            <p className="font-mono text-label text-danger">could not load usage</p>
            <p className="mt-1 text-label leading-relaxed text-ink-faint">
              {error instanceof Error ? error.message : "the backend did not answer"}
            </p>
            <Button className="mt-3" onClick={() => void mutate()}>
              RETRY
            </Button>
          </div>
        ) : isLoading && !data ? (
          <p className="text-label text-ink-faint">loading usage…</p>
        ) : !active ? (
          <p className="text-label text-ink-faint">no usage data</p>
        ) : active.total_tokens === 0 ? (
          <div className="border border-line px-4 py-6">
            <p className="font-mono text-label text-ink-muted">
              {active.detected ? "nothing recorded in this range" : `${active.label} not detected`}
            </p>
            {/* The install hint answers "why is this agent missing", which is the
                wrong question once it has been found — an agent with logs from
                last week is quiet, not absent, and saying "no matching logs"
                here would contradict the line above it. */}
            <p className="mt-1 text-label leading-relaxed text-ink-faint">
              {active.detected
                ? range === "all"
                  ? "This agent has logs, but none of them carry token counts."
                  : "Try a wider range."
                : active.install_hint}
            </p>
          </div>
        ) : (
          <div
            className={
              wide
                ? "grid gap-8 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)_minmax(0,15rem)]"
                : "flex flex-col gap-4"
            }
          >
            <div>
              <div className="flex flex-wrap items-baseline gap-2">
                <p
                  className={`font-mono font-semibold tabular-nums text-ink-bright ${
                    wide ? "text-display" : "text-title"
                  }`}
                  title={`${active.total_tokens.toLocaleString()} tokens`}
                >
                  {compactTokens(active.total_tokens)}
                </p>
                <span className="text-meta text-ink-faint">tokens</span>
                <Delta current={active.total_tokens} previous={active.previous_total_tokens} />
              </div>
              {active.previous_total_tokens !== null && PREVIOUS_LABEL[range] && (
                <p className="mt-0.5 font-mono text-micro text-ink-faint">
                  vs {PREVIOUS_LABEL[range]}: {compactTokens(active.previous_total_tokens)}
                </p>
              )}

              <TokenComposition counts={active} color={tint} className="mt-3" />

              <div className="mt-4 border-t border-line pt-3">
                <p className="font-mono text-label text-ink-muted">
                  ≈${active.estimated_cost_usd.toFixed(2)}{" "}
                  <span className="text-ink-faint">estimated</span>
                </p>
                {active.unpriced_models.length > 0 && (
                  // "some of its usage" rather than "it": in the combined view a
                  // model can be priced under one agent and unpriced under
                  // another, so the figure is a floor, not a missing number.
                  <p className="mt-1 text-meta leading-relaxed text-ink-faint">
                    excludes {active.unpriced_models.join(", ")} — some of{" "}
                    {active.unpriced_models.length === 1 ? "its" : "their"} usage has no published
                    rate.
                  </p>
                )}
              </div>
            </div>

            <div className={wide ? "flex flex-col justify-center" : ""}>
              <UsageAreaChart
                series={chartSeries}
                labels={chartLabels}
                stacked={!showingAll}
                className={wide ? "h-44" : "h-32"}
              />
              {showingAll && charted.length > 1 && (
                <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                  {charted.map((agent) => (
                    <li
                      key={agent.id}
                      className="flex items-center gap-1.5 font-mono text-micro text-ink-faint"
                    >
                      <span
                        aria-hidden
                        className="h-2 w-2 shrink-0"
                        style={{ background: agentColor(agent.id) }}
                      />
                      {agent.label}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <ModelBreakdown agent={active} tint={tint} />
          </div>
        )}
      </Panel>

      {adding && (
        <AddAgentDialog onClose={() => setAdding(false)} onAdded={() => void mutate()} />
      )}
      {confirmDialog}
    </>
  );
}
