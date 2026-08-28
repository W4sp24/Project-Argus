"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import Button from "@/components/ui/Button";
import { FIELD_CONTROL } from "@/components/ui/Field";
import { useIngestJob, type CourseSource } from "@/lib/api";
import { useCourseSelection } from "@/lib/courseSelection";

const ZONES: { key: CourseSource["zone"]; label: string }[] = [
  { key: "materials", label: "materials" },
  { key: "notes", label: "notes" },
];

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

/**
 * SOURCES rail (§4 Course Hub, left 300px) — what the course is made of, and
 * which parts of it the other two panes are working from.
 *
 * The checkboxes used to be decoration: their own docstring said so, and
 * nothing downstream read them. They now drive `useCourseSelection`, which
 * ARGUS.CHAT sends on every frame and STUDIO sends to the generators, and the
 * selection survives a reload.
 *
 * Ingestion moved here from `POST /api/study/upload` — one file, no progress,
 * no note, and (until this branch) a raw `write_bytes` with no path guard and
 * no snapshot. `+ INGEST` opens the same dialog `/sources` uses, pinned to
 * this course's `materials_path`, and the job's per-file progress renders in
 * this panel while it runs. That is the loading feedback the old dropzone
 * replaced with the word "uploading…".
 */
export default function CourseSourcesPanel({
  materialsPath,
}: {
  /** The course's real materials folder, from `GET /api/study/courses`.
   * Never built here — a literal `15-Courses/<CODE>/materials` in the
   * frontend is the bug the configurable-taxonomy refactor fixed. */
  materialsPath?: string;
}) {
  // The filter lives in the provider, not here. `ALL`/`NONE` have to mean
  // "all of what you can see", and a bulk control that cannot read the filter
  // can only mean "all of what you can't".
  const {
    available,
    visible,
    filter,
    setFilter,
    isFiltered,
    selected,
    toggle,
    selectAll,
    selectNone,
    selectAllInCourse,
    selectNoneInCourse,
    refresh,
    isLoading,
  } = useCourseSelection();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useIngestJob(jobId);

  // Refetch once the job settles: the files it wrote are new rows in this
  // list. In an effect rather than during render — `refresh` is a side
  // effect, and clearing `jobId` mid-render would drop the finished job's
  // report before the user has read it.
  const status = job?.status;
  useEffect(() => {
    if (status === "ok" || status === "partial" || status === "failed") refresh();
  }, [status, refresh]);

  const selectedCount = available.filter((source) => selected.has(source.path)).length;

  return (
    <>
      <Panel
        label={`SOURCES · ${selectedCount}/${available.length} selected`}
        headerRight={
          <Button variant="primary" size="sm" onClick={() => setDialogOpen(true)}>
            + INGEST
          </Button>
        }
      >
        {job && (
          <div className="mb-3 border border-line px-3 py-2">
            <IngestJobProgress job={job} />
          </div>
        )}

        {available.length > 0 && (
          <div className="mb-2 flex items-center gap-2">
            <input
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="filter"
              aria-label="Filter sources"
              className={`${FIELD_CONTROL} h-7 flex-1 py-0 text-meta`}
            />
            <Button size="sm" onClick={selectAll}>
              {isFiltered ? `ALL (${visible.length})` : "ALL"}
            </Button>
            <Button size="sm" onClick={selectNone}>
              {isFiltered ? `NONE (${visible.length})` : "NONE"}
            </Button>
          </div>
        )}

        {/* Under a filter, ALL/NONE act on what is on screen -- so the
            whole-course action has to be reachable and named, rather than
            being what the unqualified button silently used to do. */}
        {isFiltered && (
          <p className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-meta text-ink-muted">
            <span>
              showing {visible.length} of {available.length}
            </span>
            <button
              type="button"
              onClick={selectAllInCourse}
              className="underline underline-offset-2 transition-colors hover:text-ink"
            >
              Select all {available.length} in this course
            </button>
            <button
              type="button"
              onClick={selectNoneInCourse}
              className="underline underline-offset-2 transition-colors hover:text-ink"
            >
              Clear the whole course
            </button>
          </p>
        )}

        {selectedCount === 0 && available.length > 0 && (
          <p className="mb-2 font-mono text-meta text-warn">
            Nothing selected — chat and STUDIO have nothing to read.
          </p>
        )}

        {isLoading ? (
          <p className="text-label text-ink-faint">Reading this course…</p>
        ) : available.length === 0 ? (
          <p className="text-label text-ink-faint">
            No files for this course yet. Ingest a lecture and Argus will store it, index it, and
            write you a note from it.
          </p>
        ) : visible.length === 0 ? (
          <p className="text-label text-ink-faint">Nothing matches “{filter}”.</p>
        ) : (
          ZONES.map(({ key, label }) => {
            const rows = visible.filter((source) => source.zone === key);
            if (rows.length === 0) return null;
            return (
              <div key={key} className="mb-3 last:mb-0">
                <p className="mb-1.5 font-mono text-micro uppercase tracking-[0.16em] text-ink-faint">
                  {label} · {rows.length}
                </p>
                <ul className="space-y-1.5">
                  {rows.map((source) => (
                    <li
                      key={source.path}
                      className="flex items-start gap-2 border border-line px-2.5 py-2 transition-colors hover:border-lineHi"
                    >
                      <button
                        role="checkbox"
                        aria-checked={selected.has(source.path)}
                        aria-label={`Use ${source.title} as a source`}
                        onClick={() => toggle(source.path)}
                        className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center border transition-colors ${
                          selected.has(source.path)
                            ? "border-[var(--ac)] bg-[var(--ac)] text-void"
                            : "border-line"
                        }`}
                      >
                        {selected.has(source.path) && "✓"}
                      </button>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-label text-ink">{source.title}</p>
                        <p className="mt-0.5 font-mono text-meta text-ink-faint">
                          {relativeTime(source.modified)}
                          {source.chunks !== null &&
                            ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                          {source.chunks === null && " · not indexed"}
                        </p>
                      </div>
                      <span className="shrink-0 border border-line px-1 py-px font-mono text-micro text-ink-faint">
                        {source.kind}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}
      </Panel>

      {dialogOpen && (
        <IngestDialog
          onClose={() => setDialogOpen(false)}
          onStarted={setJobId}
          lockedTarget={materialsPath}
          // Opening this from inside a course means the point is the note.
          defaultNoteStyle="summary"
        />
      )}
    </>
  );
}
