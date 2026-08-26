"use client";

import type { IngestJob, IngestJobItem, IngestStage } from "@/lib/api";

/**
 * What an ingest is doing, per file.
 *
 * Ingestion is a pipeline, so the readout is one: every file shows the same
 * four segments and fills them as it passes through. That is the whole point
 * of the feature — a single bar for the batch would say a batch is 60% done
 * and still not say which file is where, or that one of them was skipped.
 *
 * Segments advance only on real stage transitions reported by the backend,
 * never on a timer, so a stalled file visibly stalls instead of animating
 * toward a completion that is not happening.
 */

const PIPELINE: { stage: IngestStage; label: string }[] = [
  { stage: "saving", label: "save" },
  { stage: "indexing", label: "index" },
  { stage: "summarizing", label: "summarize" },
  { stage: "done", label: "done" },
];

const ORDER: Record<IngestStage, number> = {
  queued: 0,
  saving: 1,
  indexing: 2,
  summarizing: 3,
  done: 4,
  // Terminal, and not points on the line: a failed or skipped file stopped
  // somewhere, and pretending it reached the end would be a lie.
  failed: 4,
  skipped: 4,
};

const STATUS_LABEL: Record<IngestJob["status"], string> = {
  queued: "queued",
  running: "running",
  ok: "done",
  partial: "finished with errors",
  failed: "failed",
};

function segmentClass(item: IngestJobItem, index: number): string {
  if (item.stage === "failed") return "bg-danger";
  if (item.stage === "skipped") return "bg-warn";
  const reached = ORDER[item.stage];
  if (reached > index + 1) return "bg-[var(--ac)]";
  if (reached === index + 1) return "bg-[var(--ac)] animate-blink";
  return "bg-line";
}

/** The one line under a filename that says what actually happened to it. */
function detail(item: IngestJobItem): string {
  if (item.stage === "failed") return item.error ?? "failed";
  if (item.stage === "skipped") return item.error ?? "skipped";
  if (item.stage === "done") {
    const parts = [item.chunks > 0 ? `${item.chunks} chunks` : "no chunks"];
    if (item.summary_path) parts.push("summarized");
    if (item.error) parts.push(item.error);
    return parts.join(" · ");
  }
  return `${item.stage}…`;
}

function toneClass(item: IngestJobItem): string {
  if (item.stage === "failed") return "text-danger";
  if (item.stage === "skipped") return "text-warn";
  return "text-ink-faint";
}

export default function IngestJobProgress({ job }: { job: IngestJob }) {
  const items = job.items ?? [];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">
          {job.done} of {job.total} · into {job.target}
        </p>
        <p
          className={`font-mono text-meta uppercase tracking-[0.16em] ${
            job.status === "failed"
              ? "text-danger"
              : job.status === "partial"
                ? "text-warn"
                : "text-[var(--ac)]"
          }`}
          // One live region for the whole job: announcing every file's every
          // stage would talk over the user for the length of a batch.
          aria-live="polite"
        >
          {STATUS_LABEL[job.status]}
        </p>
      </div>

      <ul className="flex flex-col gap-3">
        {items.map((item) => (
          <li key={item.id}>
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 truncate text-label text-ink">{item.filename}</p>
              <p className={`shrink-0 font-mono text-meta ${toneClass(item)}`}>{detail(item)}</p>
            </div>
            <div className="mt-1.5 flex gap-px" aria-hidden>
              {PIPELINE.map((segment, index) => (
                <span
                  key={segment.stage}
                  className={`h-0.5 flex-1 transition-colors ${segmentClass(item, index)}`}
                />
              ))}
            </div>
          </li>
        ))}
      </ul>

      {job.error && job.status === "failed" && (
        <p className="mt-3 font-mono text-meta text-danger">{job.error}</p>
      )}
    </div>
  );
}
