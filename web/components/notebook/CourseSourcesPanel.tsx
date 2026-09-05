"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Panel from "@/components/Panel";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import Button from "@/components/ui/Button";
import { FIELD_CONTROL } from "@/components/ui/Field";
import { useFlashcardDecks, useIngestJob, type CourseSource } from "@/lib/api";
import { useCourseSelection } from "@/lib/courseSelection";
import { formatRelativeTime } from "@/lib/relativeTime";

const ZONES: { key: CourseSource["zone"]; label: string }[] = [
  { key: "materials", label: "materials" },
  { key: "notes", label: "notes" },
];

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
  code,
  materialsPath,
}: {
  /** The course this rail belongs to, so a row can say how many decks came out
   * of it. The same SWR key STUDIO already holds, so the count is free. */
  code: string;
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
    excluded,
    visible,
    filter,
    setFilter,
    isFiltered,
    selected,
    toggle,
    selectAll,
    selectRange,
    selectNone,
    selectAllInCourse,
    selectNoneInCourse,
    refresh,
    isLoading,
  } = useCourseSelection();

  const { data: decks } = useFlashcardDecks(code);
  /** How many decks were generated from each file, counted once for the whole
   * rail rather than per row.
   *
   * The join needs no normalisation: `source_paths` is written from the
   * resolved corpus, and `course_corpus` filters on the very strings this rail
   * ticks, so the path written is the path rendered. */
  const deckCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const deck of decks ?? []) {
      for (const path of deck.source_paths) {
        counts.set(path, (counts.get(path) ?? 0) + 1);
      }
    }
    return counts;
  }, [decks]);

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

  // The rows in the order they appear, flattened across zone groups. A range
  // has to be computed over what is on screen: doing it over `available`
  // would tick rows the filter is hiding, which is exactly the ALL/NONE bug
  // in a new place.
  const ordered = useMemo(
    () => ZONES.flatMap(({ key }) => visible.filter((source) => source.zone === key)),
    [visible],
  );
  const lastToggled = useRef<string | null>(null);

  function pick(path: string, event: { shiftKey: boolean }) {
    const anchor = lastToggled.current;
    if (event.shiftKey && anchor && anchor !== path) {
      const from = ordered.findIndex((source) => source.path === anchor);
      const to = ordered.findIndex((source) => source.path === path);
      if (from !== -1 && to !== -1) {
        const [start, end] = from < to ? [from, to] : [to, from];
        selectRange(ordered.slice(start, end + 1).map((source) => source.path));
        lastToggled.current = path;
        return;
      }
    }
    lastToggled.current = path;
    toggle(path);
  }

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
            <IngestJobProgress job={job} onDismiss={() => setJobId(null)} />
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

        {available.length > 1 && (
          <p className="mb-2 font-mono text-micro text-ink-muted">
            shift-click to select a run
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
                  {rows.map((source) => {
                    const fromHere = deckCounts.get(source.path) ?? 0;
                    return (
                    <li
                      key={source.path}
                      className="border border-line transition-colors hover:border-lineHi"
                    >
                      {/* The whole row is the control, not just the 14px box.
                          The `<li>` already advertised itself as interactive
                          with `hover:border-lineHi` while carrying no handler
                          at all, and the only real target was under a third of
                          the 44px minimum on both axes — the affordance and the
                          target disagreed. Promoting the button to the row
                          settles both at no layout cost: the padding and flex
                          simply moved off the `<li>` and onto it.

                          It stays a single `<button role="checkbox">` rather
                          than a `<label>` wrapping an input, because a label
                          would either introduce a second checkbox or fold the
                          row's text into the accessible name. `aria-label`
                          overrides the content here, so the name is exactly
                          "Use <title> as a source" no matter what the row
                          renders. The children are `<span>`s for the same
                          reason a `<p>` cannot live inside a `<button>`:
                          phrasing content only. */}
                      <button
                        role="checkbox"
                        aria-checked={selected.has(source.path)}
                        aria-label={`Use ${source.title} as a source`}
                        onClick={(event) => pick(source.path, event)}
                        className="flex w-full items-start gap-2 px-2.5 py-2 text-left"
                      >
                        <span
                          aria-hidden
                          className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center border transition-colors ${
                            selected.has(source.path)
                              ? "border-[var(--ac)] bg-[var(--ac)] text-void"
                              : "border-line"
                          }`}
                        >
                          {selected.has(source.path) && "✓"}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-label text-ink">{source.title}</span>
                          <span className="mt-0.5 block font-mono text-meta text-ink-faint">
                            {formatRelativeTime(source.modified)}
                            {source.chunks !== null &&
                              ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                            {source.chunks === null && " · not indexed"}
                          </span>
                        </span>
                        {fromHere > 0 && (
                          <span className="shrink-0 border border-[var(--ac)] px-1 py-px font-mono text-micro text-[var(--ac)]">
                            {fromHere} deck{fromHere === 1 ? "" : "s"}
                          </span>
                        )}
                        <span className="shrink-0 border border-line px-1 py-px font-mono text-micro text-ink-faint">
                          {source.kind}
                        </span>
                      </button>
                    </li>
                    );
                  })}
                </ul>
              </div>
            );
          })
        )}

        {/* Shown as context, never selectable. The exclusion is deliberate and
            well argued -- Argus's own guides and exams fed back in as sources
            are a loop, not context -- but the rail simply did not mention that
            a third zone existed, so a user who had just generated a study
            guide looked for it here and found nothing. */}
        {excluded.length > 0 && (
          <div className="mt-3 border-t border-line pt-3">
            <p className="mb-1.5 font-mono text-micro uppercase tracking-[0.16em] text-ink-faint">
              study · {excluded.length}
            </p>
            <ul className="space-y-1">
              {excluded.map((source) => (
                <li key={source.path} className="truncate text-meta text-ink-muted">
                  {source.title}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 font-mono text-micro text-ink-muted">
              Argus&apos;s own output — not used as a source.
            </p>
          </div>
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
