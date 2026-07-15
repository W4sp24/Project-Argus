"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import UsageBlock from "@/components/UsageBlock";
import { useCliUsage, type CliUsageRange } from "@/lib/api";

const VIEWS: CliUsageRange[] = ["today", "week", "all"];
const VIEW_LABEL: Record<CliUsageRange, string> = { today: "TODAY", week: "WEEK", all: "ALL" };

function chartLabel(range: CliUsageRange, label: string): string {
  if (range === "all") return label; // already "YYYY-wNN"
  return label.slice(5); // "MM-DD" from "YYYY-MM-DD"
}

/**
 * CLAUDE CODE — account-wide Claude Code CLI usage, parsed from local
 * `~/.claude/projects/**\/*.jsonl` transcripts (backend/cli_usage.py), wired
 * to `GET /api/usage/cli`. Deliberately a separate panel from TOKENS.CLAUDE:
 * this is real account-wide token spend across every local Claude Code
 * session, not just what Argus's own chat/planner/study-generate calls used.
 */
export default function CliUsage() {
  const [view, setView] = useState<CliUsageRange>("today");
  const { data, isLoading } = useCliUsage(view);

  return (
    <Panel
      label="CLAUDE CODE"
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
        series={(data?.series ?? []).map((point) => ({
          label: chartLabel(view, point.label),
          value: point.total_tokens,
        }))}
        rows={(data?.models ?? []).map((model) => ({
          label: model.model,
          value: model.total_tokens,
        }))}
        emptyMessage="no local Claude Code sessions found"
      />
    </Panel>
  );
}
