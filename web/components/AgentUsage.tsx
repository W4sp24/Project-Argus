"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import UsageAreaChart from "@/components/charts/UsageAreaChart";
import TokenComposition from "@/components/usage/TokenComposition";
import { useAgentUsage, type AgentUsage as AgentUsageSlice, type CliUsageRange } from "@/lib/api";
import { agentColor, compactTokens } from "@/lib/agentPalette";

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
              <span className="min-w-0 flex-1 truncate font-mono text-meta text-ink-muted" title={model.model}>
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
 * AGENT.USAGE — account-wide token spend across every local coding agent
 * Argus can read, wired to `GET /api/usage/agents`.
 *
 * Was CLAUDE CODE, and Claude-Code-only, which stopped making sense once
 * Argus itself ran on any model. Agents that are not installed still get a
 * tab: it renders dimmed with an install hint, because a tab that disappears
 * when a tool is missing reads as a bug rather than as information.
 *
 * Distinct from ARGUS.USAGE (`TokenUsage.tsx`), which is only what Argus's own
 * chat/planner/study-generate calls spent. These are deliberately not summed
 * into one grand total — they answer different questions.
 */
export default function AgentUsage({ size = "default" }: { size?: "default" | "large" }) {
  const [range, setRange] = useState<CliUsageRange>("today");
  const [agentId, setAgentId] = useState<string>(COMBINED_ID);
  const { data, isLoading } = useAgentUsage(range);

  const agents = data?.agents ?? [];
  const active: AgentUsageSlice | undefined =
    agentId === COMBINED_ID ? data?.combined : agents.find((agent) => agent.id === agentId);

  const tint = agentId === COMBINED_ID ? "var(--ac)" : agentColor(agentId);
  const wide = size === "large";

  const tabs: { id: string; label: string; detected: boolean; total: number }[] = [
    {
      id: COMBINED_ID,
      label: "ALL",
      detected: data?.combined.detected ?? false,
      total: data?.combined.total_tokens ?? 0,
    },
    ...agents.map((agent) => ({
      id: agent.id,
      label: agent.label.toUpperCase(),
      detected: agent.detected,
      total: agent.total_tokens,
    })),
  ];

  return (
    <Panel
      label="AGENT.USAGE"
      className={wide ? "min-h-[22rem]" : undefined}
      headerRight={
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
      }
    >
      {/* Agent rail. Undetected agents stay clickable so their empty state can
          explain itself instead of the tab simply not being there. */}
      <div className="-mt-1 mb-4 flex flex-wrap gap-1.5">
        {tabs.map((tab) => {
          const selected = tab.id === agentId;
          const dot = tab.id === COMBINED_ID ? "var(--ac)" : agentColor(tab.id);
          return (
            <button
              key={tab.id}
              type="button"
              aria-pressed={selected}
              onClick={() => setAgentId(tab.id)}
              className={`flex items-center gap-2 border px-2.5 py-1 transition-colors ${
                selected ? "border-lineHi bg-sunken" : "border-line hover:border-lineHi"
              } ${tab.detected ? "" : "opacity-55"}`}
            >
              <span
                aria-hidden
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${tab.detected ? "" : "opacity-40"}`}
                style={{ background: tab.detected ? dot : "currentColor" }}
              />
              <span
                className={`font-mono text-micro uppercase tracking-[0.14em] ${
                  selected ? "text-ink-bright" : "text-ink-muted"
                }`}
              >
                {tab.label}
              </span>
              <span className="font-mono text-micro tabular-nums text-ink-faint">
                {tab.detected ? compactTokens(tab.total) : "—"}
              </span>
            </button>
          );
        })}
      </div>

      {isLoading && !data ? (
        <p className="text-label text-ink-faint">loading usage…</p>
      ) : !active ? (
        <p className="text-label text-ink-faint">no usage data</p>
      ) : active.total_tokens === 0 ? (
        <div className="border border-line px-4 py-6">
          <p className="font-mono text-label text-ink-muted">
            {active.detected ? "nothing recorded in this range" : `${active.label} not detected`}
          </p>
          <p className="mt-1 text-label leading-relaxed text-ink-faint">{active.install_hint}</p>
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
                  wide ? "text-4xl" : "text-2xl"
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
                <p className="mt-1 text-meta leading-relaxed text-ink-faint">
                  excludes {active.unpriced_models.join(", ")} — Argus has no published rate for
                  {active.unpriced_models.length === 1 ? " it" : " them"}.
                </p>
              )}
            </div>
          </div>

          <div className={wide ? "flex flex-col justify-center" : ""}>
            <UsageAreaChart
              points={active.series.map((point) => ({
                ...point,
                label: chartLabel(range, point.label),
              }))}
              color={tint}
              className={wide ? "h-44" : "h-32"}
            />
          </div>

          <ModelBreakdown agent={active} tint={tint} />
        </div>
      )}
    </Panel>
  );
}
