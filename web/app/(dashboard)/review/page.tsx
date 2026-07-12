"use client";

import { useState } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";
import { fetcher } from "@/lib/api";

interface ScheduleBlock {
  title: string;
  start: string;
  end: string;
}

interface Suggestion {
  id: number;
  created_at: string;
  kind: "schedule" | "task" | "note";
  payload: Record<string, unknown>;
  rationale: string;
}

const KIND_STYLE: Record<string, string> = {
  schedule: "bg-signal/15 text-signal",
  task: "bg-primary/20 text-primary-soft",
  note: "bg-accent/15 text-accent",
};

function blockTime(iso: string): string {
  const parsed = new Date(iso);
  return isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/30 p-3 font-mono text-[12px] leading-relaxed">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+")
              ? "text-emerald-300"
              : line.startsWith("-")
                ? "text-rose-300"
                : line.startsWith("@@")
                  ? "text-primary-soft"
                  : "text-ink-faint"
          }
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export default function ReviewPage() {
  const { data: suggestions, mutate } = useSWR<Suggestion[]>("/api/review", fetcher);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  async function act(id: number, action: "approve" | "dismiss", reason?: string) {
    setBusy(id);
    const response = await fetch(`/api/review/${id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: action === "dismiss" ? JSON.stringify({ reason: reason ?? "" }) : "{}",
    });
    const payload = await response.json();
    setStatus(
      response.ok
        ? action === "approve"
          ? `#${id} applied — logged to today's daily note.`
          : `#${id} dismissed.`
        : `#${id}: ${payload.detail}`,
    );
    setBusy(null);
    mutate();
  }

  return (
    <>
      <PageHeader
        label="REVIEW"
        title="Approval queue"
        subtitle="Nothing touches your vault, calendar, or Todoist without your click. Every apply is git-snapshotted first."
      />

      {status && (
        <p className="mb-4 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-ink-muted">
          {status}
        </p>
      )}

      {suggestions && suggestions.length === 0 && (
        <GlassCard label="QUEUE" title="Nothing pending">
          <p className="text-sm text-ink-muted">
            Ask FRIDAY to <span className="font-mono text-xs text-primary-soft">/plan</span> your
            day in Chat, or import a syllabus — proposals land here.
          </p>
        </GlassCard>
      )}

      <div className="space-y-4">
        {(suggestions ?? []).map((suggestion) => (
          <GlassCard key={suggestion.id} className="animate-rise">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${KIND_STYLE[suggestion.kind]}`}
              >
                {suggestion.kind}
              </span>
              <span className="font-mono text-[11px] text-ink-faint">#{suggestion.id}</span>
              <span className="ml-auto font-mono text-[11px] text-ink-faint">
                {suggestion.created_at}
              </span>
            </div>

            <p className="mb-3 text-sm text-ink">{suggestion.rationale}</p>

            {suggestion.kind === "schedule" && (
              <ul className="mb-3 space-y-1.5">
                {((suggestion.payload.blocks as ScheduleBlock[]) ?? []).map((block, i) => (
                  <li key={i} className="flex items-center gap-3 text-sm">
                    <span className="w-32 shrink-0 font-mono text-[11px] text-signal">
                      {blockTime(block.start)} – {blockTime(block.end)}
                    </span>
                    <span className="h-6 w-1 rounded bg-gradient-to-b from-primary to-accent" />
                    <span className="text-ink-muted">{block.title}</span>
                  </li>
                ))}
              </ul>
            )}

            {suggestion.kind === "task" && (
              <div className="mb-3 space-y-1 font-mono text-[12px]">
                <p className="rounded-lg bg-rose-500/10 px-3 py-1.5 text-rose-300">
                  − {String(suggestion.payload.old_line)}
                </p>
                <p className="rounded-lg bg-emerald-500/10 px-3 py-1.5 text-emerald-300">
                  + {String(suggestion.payload.new_line)}
                </p>
                <p className="text-ink-faint">
                  {String(suggestion.payload.path)}:{String(suggestion.payload.line)}
                </p>
              </div>
            )}

            {suggestion.kind === "note" && (
              <div className="mb-3">
                <p className="mb-1 font-mono text-[11px] text-ink-faint">
                  {String(suggestion.payload.path)}
                </p>
                <DiffView diff={String(suggestion.payload.diff)} />
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => act(suggestion.id, "approve")}
                disabled={busy !== null}
                className="rounded-xl bg-gradient-to-r from-primary to-accent px-5 py-2 font-display text-sm text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {busy === suggestion.id ? "Applying…" : "Approve"}
              </button>
              <button
                onClick={() => {
                  const reason = window.prompt("Why dismiss? (feeds back to the planner)") ?? "";
                  act(suggestion.id, "dismiss", reason);
                }}
                disabled={busy !== null}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-5 py-2 text-sm text-ink-muted transition-colors hover:border-accent/40 hover:text-ink disabled:opacity-40"
              >
                Dismiss
              </button>
            </div>
          </GlassCard>
        ))}
      </div>
    </>
  );
}
