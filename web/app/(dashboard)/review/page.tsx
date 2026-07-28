"use client";

import { useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import PageHeader from "@/components/PageHeader";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import { apiFetch, fetcher } from "@/lib/api";

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
  schedule: "border-mode-study text-mode-study",
  task: "border-[var(--ac)] text-[var(--ac)]",
  note: "border-mode-research text-mode-research",
};

function blockTime(iso: string): string {
  const parsed = new Date(iso);
  return isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="overflow-x-auto border border-line bg-sunken p-3 font-mono text-label leading-relaxed">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+")
              ? "text-ok"
              : line.startsWith("-")
                ? "text-danger"
                : line.startsWith("@@")
                  ? "text-[var(--ac)]"
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
  const { confirm, confirmDialog } = useConfirm();

  async function act(id: number, action: "approve" | "dismiss", reason?: string) {
    setBusy(id);
    const response = await apiFetch(`/api/review/${id}/${action}`, {
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

  /**
   * Why the suggestion was wrong is the most useful thing the planner can
   * learn, so it gets a real field instead of a browser prompt. Cancelling
   * now cancels: `window.prompt` returned null on cancel, which the old code
   * coerced to `""` and dismissed anyway.
   */
  async function dismiss(id: number) {
    const reason = await confirm({
      label: "Dismiss suggestion",
      message: "Dismiss this suggestion?",
      detail: "Your reason goes back to the planner so it stops proposing the same thing.",
      confirmLabel: "Dismiss",
      reason: {
        label: "why?",
        placeholder: "already scheduled it · wrong course · not this week",
      },
    });
    if (reason === null) return;
    await act(id, "dismiss", reason);
  }

  return (
    <>
      <PageHeader
        label="REVIEW"
        title="Approval queue"
        subtitle="Nothing touches your vault, calendar, or Todoist without your click. Every apply is git-snapshotted first."
      />

      {status && (
        <p className="mb-4 border border-line bg-panel px-4 py-3 text-sm text-ink-muted">{status}</p>
      )}

      {suggestions && suggestions.length === 0 && (
        <Panel label="QUEUE" title="Nothing pending">
          <p className="text-sm text-ink-muted">
            Ask Argus to <span className="font-mono text-xs text-[var(--ac)]">/plan</span> your
            day in Chat, or import a syllabus — proposals land here.
          </p>
        </Panel>
      )}

      <div className="space-y-4">
        {(suggestions ?? []).map((suggestion) => (
          <Panel key={suggestion.id} className="animate-rise">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span
                className={`border px-2.5 py-0.5 font-mono text-meta uppercase tracking-wide ${KIND_STYLE[suggestion.kind]}`}
              >
                {suggestion.kind}
              </span>
              <span className="font-mono text-label text-ink-faint">#{suggestion.id}</span>
              <span className="ml-auto font-mono text-label text-ink-faint">
                {suggestion.created_at}
              </span>
            </div>

            <p className="mb-3 text-sm text-ink">{suggestion.rationale}</p>

            {suggestion.kind === "schedule" && (
              <ul className="mb-3 space-y-1.5">
                {((suggestion.payload.blocks as ScheduleBlock[]) ?? []).map((block, i) => (
                  <li key={i} className="flex items-center gap-3 text-sm">
                    <span className="w-32 shrink-0 font-mono text-label text-mode-study">
                      {blockTime(block.start)} – {blockTime(block.end)}
                    </span>
                    <span className="h-6 w-1 bg-[var(--ac)]" />
                    <span className="text-ink-muted">{block.title}</span>
                  </li>
                ))}
              </ul>
            )}

            {suggestion.kind === "task" && (
              <div className="mb-3 space-y-1 font-mono text-label">
                <p className="border border-danger/30 bg-danger/10 px-3 py-1.5 text-danger">
                  − {String(suggestion.payload.old_line)}
                </p>
                <p className="border border-ok/30 bg-ok/10 px-3 py-1.5 text-ok">
                  + {String(suggestion.payload.new_line)}
                </p>
                <p className="text-ink-faint">
                  {String(suggestion.payload.path)}:{String(suggestion.payload.line)}
                </p>
              </div>
            )}

            {suggestion.kind === "note" && (
              <div className="mb-3">
                <p className="mb-1 font-mono text-label text-ink-faint">
                  {String(suggestion.payload.path)}
                </p>
                <DiffView diff={String(suggestion.payload.diff)} />
              </div>
            )}

            <div className="flex gap-2">
              <Button
                size="md"
                variant="primary"
                onClick={() => act(suggestion.id, "approve")}
                disabled={busy !== null}
              >
                {busy === suggestion.id ? "Applying…" : "Approve"}
              </Button>
              <Button size="md" onClick={() => dismiss(suggestion.id)} disabled={busy !== null}>
                Dismiss
              </Button>
            </div>
          </Panel>
        ))}
      </div>
      {confirmDialog}
    </>
  );
}
