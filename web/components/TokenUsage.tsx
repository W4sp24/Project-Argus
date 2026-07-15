"use client";

import { useState } from "react";
import MiniLineChart from "@/components/charts/MiniLineChart";
import Panel from "@/components/Panel";
import { TOKEN_USAGE_MOCK } from "@/components/preview/tokenUsageMock";
import { FLAGS } from "@/lib/flags";

const VIEWS = ["session", "week", "all"] as const;
type View = (typeof VIEWS)[number];

const VIEW_LABEL: Record<View, string> = { session: "SESSION", week: "WEEK", all: "ALL" };

/**
 * TOKENS.CLAUDE (§14) [PREVIEW] — SESSION / WEEK / ALL segmented view of
 * Claude token spend. Mock data until `backend/agent/` logs real usage and
 * `GET /api/usage` exists (§8 flags.tokenUsage). One SVG line chart, no
 * recharts (§10).
 */
export default function TokenUsage() {
  const [view, setView] = useState<View>("session");
  const data = TOKEN_USAGE_MOCK[view];
  const pctOfCap = Math.min(100, Math.round((data.totalTokens / data.capTokens) * 100));

  return (
    <Panel
      label="TOKENS.CLAUDE"
      preview={FLAGS.tokenUsage === "preview"}
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
      <p className="font-mono text-2xl font-semibold text-ink-bright">
        {data.totalTokens.toLocaleString()}
        <span className="ml-1.5 text-xs font-normal text-ink-faint">tokens</span>
      </p>
      <p className="mt-1 font-mono text-[11px] text-ink-muted">
        in {data.inputTokens.toLocaleString()} · out {data.outputTokens.toLocaleString()} · ≈$
        {data.costUsd.toFixed(2)}
      </p>

      <div className="mt-2">
        <div className="h-1 w-full bg-sunken">
          <div className="h-1 bg-[var(--ac)]" style={{ width: `${pctOfCap}%` }} />
        </div>
        <p className="mt-1 font-mono text-[10px] text-ink-faint">
          {pctOfCap}% of cap · {data.rangeLabel}
        </p>
      </div>

      <div className="mt-3">
        <MiniLineChart values={data.chart} labels={data.axisLabels} />
      </div>

      <ul className="mt-3 space-y-1 border-t border-line pt-2">
        {data.features.map((feature) => (
          <li key={feature.name} className="flex items-center justify-between font-mono text-[11px]">
            <span className="uppercase tracking-wide text-ink-faint">{feature.name}</span>
            <span className="text-ink-muted">{feature.tokens.toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
