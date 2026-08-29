"use client";

import { useVault, type IngestJob, type IngestJobItem, type IngestStage } from "@/lib/api";
import { obsidianUri } from "@/lib/citations";

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
  // The wire stage is still `summarizing` — it lives in a CHECK constraint,
  // and rebuilding the table to rename it would buy a label the UI can say
  // for itself. What the user sees is what actually gets written: a note.
  { stage: "summarizing", label: "note" },
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

/** What the panel *around* this readout should call itself.
 *
 * The header used to be the literal string "INGESTING" and nothing ever
 * changed it, so a finished job sat under a heading claiming it was still
 * running while the status beside it read "done". Exported so the label and
 * the status come from one place rather than drifting apart again. */
export function jobPanelLabel(job: IngestJob): string {
  if (job.status === "queued" || job.status === "running") return "INGESTING";
  if (job.status === "partial") return "INGESTED WITH ERRORS";
  if (job.status === "failed") return "INGEST FAILED";
  return "INGESTED";
}

function segmentClass(item: IngestJobItem, index: number): string {
  // Where it stopped, not merely that it stopped. Colouring every segment for
  // a failure made a file that saved and indexed and lost only its note look
  // identical to one that was never written at all -- so a user whose note
  // failed concluded their file was gone. Segments before the break stay in
  // the accent because those stages really did succeed.
  if (item.failed_stage) {
    const broke = ORDER[item.failed_stage];
    if (index + 1 < broke) return "bg-[var(--ac)]";
    if (index + 1 === broke) return item.stage === "skipped" ? "bg-warn" : "bg-danger";
    return "bg-line";
  }
  // No recorded stage: either it is still moving, or it is an older job from
  // before the column existed, where all we can honestly say is "stopped".
  if (item.stage === "failed") return "bg-danger";
  if (item.stage === "skipped") return "bg-warn";
  const reached = ORDER[item.stage];
  if (reached > index + 1) return "bg-[var(--ac)]";
  if (reached === index + 1) return "bg-[var(--ac)] animate-blink";
  return "bg-line";
}

/** The pipeline bar is `aria-hidden`, so the stage that broke has to reach the
 * text too — colour alone is not an accessible way to say it. */
function stoppedAt(item: IngestJobItem): string | null {
  const label = PIPELINE.find((step) => step.stage === item.failed_stage)?.label;
  return label ? `while ${label === "note" ? "writing the note" : `${label}ing`}` : null;
}

/** The one line under a filename that says what actually happened to it. */
function detail(item: IngestJobItem): string {
  const where = stoppedAt(item);
  if (item.stage === "failed") {
    const failed = where ? `failed ${where}` : "failed";
    return item.error ? `${failed} — ${item.error}` : failed;
  }
  if (item.stage === "skipped") return item.error ?? "skipped";
  if (item.stage === "done") {
    const parts = [item.chunks > 0 ? `${item.chunks} chunks` : "no chunks"];
    // Saved and indexed, and only the note missing. Say both halves: the file
    // is in the vault and searchable, which is the part the old readout hid.
    if (item.failed_stage) parts.push(`saved and indexed, but the note failed`);
    else if (item.summary_path) parts.push("note written");
    if (item.error) parts.push(item.error);
    return parts.join(" · ");
  }
  return `${item.stage}…`;
}

function toneClass(item: IngestJobItem): string {
  if (item.stage === "failed") return "text-danger";
  if (item.stage === "skipped") return "text-warn";
  // A partial outcome is a warning, not a failure: the file is fine.
  if (item.failed_stage) return "text-warn";
  return "text-ink-faint";
}

export default function IngestJobProgress({
  job,
  onDismiss,
}: {
  job: IngestJob;
  /** Clears the readout. Deliberately not automatic: the completion summary
   * is the receipt, and it carries the link to what was written. Without one
   * of these the panel simply never went away -- `jobId` was only ever set,
   * so a second ingest silently replaced the first job's report. */
  onDismiss?: () => void;
}) {
  const settled = job.status === "ok" || job.status === "partial" || job.status === "failed";
  const items = job.items ?? [];
  const { data: vault } = useVault();
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
        {settled && onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="font-mono text-meta uppercase tracking-[0.16em] text-ink-muted underline underline-offset-2 transition-colors hover:text-ink"
          >
            dismiss
          </button>
        )}
      </div>

      <ul className="flex flex-col gap-3">
        {items.map((item) => (
          <li key={item.id}>
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 truncate text-label text-ink">{item.filename}</p>
              <p className={`shrink-0 font-mono text-meta ${toneClass(item)}`}>
                {detail(item)}
              </p>
            </div>
            {/* The note is what the whole feature exists to produce, and its
                path used to live in a `title` attribute -- hover-only,
                unreachable by keyboard, invisible on touch, and not announced
                by a screen reader. /sources already renders obsidian links for
                its rows; the capability was three files away and unused for
                the one artifact that matters most. */}
            {item.summary_path && vault && (
              <a
                href={obsidianUri(vault.path, item.summary_path)}
                aria-label={`Open the note written for ${item.filename} in Obsidian`}
                className="mt-0.5 inline-block max-w-full truncate font-mono text-meta text-ink-muted underline underline-offset-2 transition-colors hover:text-[var(--ac)]"
              >
                {item.summary_path} ↗
              </a>
            )}
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
