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
        <strong key={i} className="font-display text-primary-soft">
          {part}
        </strong>
      ) : (
        part
      ),
    );
  return (
    <div className="space-y-1.5 text-sm text-ink-muted">
      {markdown.split("\n").map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        if (trimmed.startsWith("- ")) {
          return (
            <p key={i} className="flex items-baseline gap-2 pl-1">
              <span className="text-ink-faint">•</span>
              <span>{bold(trimmed.slice(2))}</span>
            </p>
          );
        }
        return <p key={i}>{bold(trimmed)}</p>;
      })}
    </div>
  );
}

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
    <Panel label="BRIEFING" title="Your morning briefing">
      {briefing ? (
        <>
          {!collapsed && <BriefingBody markdown={briefing.markdown} />}
          <p className="mt-3 font-mono text-[11px] text-ink-faint">
            written to {briefing.path} ·{" "}
            <button
              onClick={toggleCollapsed}
              className="text-primary-soft underline-offset-2 hover:underline"
            >
              {collapsed ? "expand" : "collapse"}
            </button>{" "}
            ·{" "}
            <button
              onClick={generateBriefing}
              disabled={generating}
              className="text-primary-soft underline-offset-2 hover:underline disabled:opacity-40"
            >
              {generating ? "composing…" : "run again"}
            </button>
          </p>
        </>
      ) : briefingMissing ? (
        <div className="flex flex-wrap items-center gap-4">
          <p className="text-sm text-ink-muted">
            No briefing yet today — Argus writes one into your daily note at 07:00, or on demand.
          </p>
          <button
            onClick={generateBriefing}
            disabled={generating}
            className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2 font-display text-sm text-white disabled:opacity-40"
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
