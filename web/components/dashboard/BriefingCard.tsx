"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { fetcher } from "@/lib/api";

interface Briefing {
  date: string;
  path: string;
  markdown: string;
}

/** Minimal renderer for the briefing's markdown subset (bold labels + bullets). */
function BriefingBody({ markdown }: { markdown: string }) {
  const bold = (text: string) =>
    text.split(/\*\*(.+?)\*\*/g).map((part, i) =>
      i % 2 === 1 ? (
        <strong key={i} className="font-medium text-ink-bright">
          {part}
        </strong>
      ) : (
        part
      ),
    );
  return (
    <div className="space-y-1.5 text-[13.5px] leading-relaxed text-ink-muted">
      {markdown.split("\n").map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        if (trimmed.startsWith("- ")) {
          return (
            <p key={i} className="flex items-baseline gap-2 pl-1">
              <span className="text-ink-faint">·</span>
              <span>{bold(trimmed.slice(2))}</span>
            </p>
          );
        }
        return <p key={i}>{bold(trimmed)}</p>;
      })}
    </div>
  );
}

/** ARGUS.AGENT (§4 General, right rail) — briefing summary + actions, restyled BriefingCard. */
export default function BriefingCard() {
  const {
    data: briefing,
    error: briefingMissing,
    mutate: refreshBriefing,
  } = useSWR<Briefing>("/api/briefing", fetcher, { shouldRetryOnError: false });
  const [generating, setGenerating] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const storageKey = `argus-briefing-collapsed-${new Date().toISOString().slice(0, 10)}`;

  useEffect(() => {
    setCollapsed(localStorage.getItem(storageKey) === "1");
  }, [storageKey]);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(storageKey, next ? "1" : "0");
  }

  async function generateBriefing() {
    setGenerating(true);
    try {
      await fetch("/api/briefing/run", { method: "POST" });
      await refreshBriefing();
    } finally {
      setGenerating(false);
    }
  }

  return (
    <Panel label="ARGUS.AGENT">
      {briefing ? (
        <>
          {!collapsed && <BriefingBody markdown={briefing.markdown} />}
          <p className="mt-3 font-mono text-[10px] text-ink-faint">
            written to {briefing.path} ·{" "}
            <button onClick={toggleCollapsed} className="text-[var(--ac)] underline-offset-2 hover:underline">
              {collapsed ? "expand" : "collapse"}
            </button>{" "}
            ·{" "}
            <button
              onClick={generateBriefing}
              disabled={generating}
              className="text-[var(--ac)] underline-offset-2 hover:underline disabled:opacity-40"
            >
              {generating ? "composing…" : "run again"}
            </button>
          </p>
        </>
      ) : briefingMissing ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[13px] text-ink-muted">
            No briefing yet today — Argus writes one into your daily note at 07:00, or on demand.
          </p>
          <button
            onClick={generateBriefing}
            disabled={generating}
            className="shrink-0 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            {generating ? "Composing…" : "Generate now"}
          </button>
        </div>
      ) : (
        <p className="text-sm text-ink-faint">Loading…</p>
      )}
    </Panel>
  );
}
