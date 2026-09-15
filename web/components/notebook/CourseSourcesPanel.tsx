"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import { useFlashcardDecks, useIngestJob, type CourseSource } from "@/lib/api";
import { useCourseSelection } from "@/lib/courseSelection";
import { formatRelativeTime } from "@/lib/relativeTime";

/** ALL / NONE. Uppercase on purpose: they are the two terse controls in a rail
 *  of sentences, and the e2e suite addresses them by exactly these names. */
const RAIL_ACTION =
  "min-h-8 shrink-0 rounded-ctl border border-nb-line px-2.5 py-1.5 text-label text-nb-ink transition-colors hover:border-nb-lineHi";

const ZONES: { key: CourseSource["zone"]; label: string }[] = [
  { key: "materials", label: "Materials" },
  { key: "notes", label: "Notes" },
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
      <NotebookPanel
        heading="Sources"
        scale="body"
        pad="md"
        headerRight={
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            className="rounded-ctl border border-[var(--ac)] bg-nb-acBg px-2.5 py-1.5 text-label font-semibold text-[var(--ac)] transition-opacity hover:opacity-80"
          >
            ＋ Add
          </button>
        }
      >
        {/* The count is a status line, not part of the heading: it changes on
            every tick, and a heading that renames itself is one a test cannot
            hold on to. */}
        {available.length > 0 && (
          <p className="mb-3 rounded-ctl bg-nb-acBg px-3 py-2 text-label text-[var(--ac)]">
            Reading {selectedCount} of {available.length} files
          </p>
        )}

        {job && (
          <div className="mb-3 rounded-tile border border-nb-line px-3 py-2">
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
              className="min-h-8 min-w-0 flex-1 rounded-ctl border border-nb-line bg-nb-void px-3 py-1.5 text-label text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
            />
            <button type="button" onClick={selectAll} className={RAIL_ACTION}>
              {isFiltered ? `ALL (${visible.length})` : "ALL"}
            </button>
            <button type="button" onClick={selectNone} className={RAIL_ACTION}>
              {isFiltered ? `NONE (${visible.length})` : "NONE"}
            </button>
          </div>
        )}

        {/* Under a filter, ALL/NONE act on what is on screen -- so the
            whole-course action has to be reachable and named, rather than
            being what the unqualified button silently used to do. */}
        {isFiltered && (
          <p className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-label text-nb-body">
            <span>
              showing {visible.length} of {available.length}
            </span>
            <button
              type="button"
              onClick={selectAllInCourse}
              className="underline underline-offset-2 transition-colors hover:text-nb-ink"
            >
              Select all {available.length} in this course
            </button>
            <button
              type="button"
              onClick={selectNoneInCourse}
              className="underline underline-offset-2 transition-colors hover:text-nb-ink"
            >
              Clear the whole course
            </button>
          </p>
        )}

        {available.length > 1 && (
          <p className="mb-2 text-label text-nb-faint">Shift-click to select a run.</p>
        )}

        {selectedCount === 0 && available.length > 0 && (
          <p className="mb-2 text-label text-warn">
            Nothing selected — chat and the generators have nothing to read.
          </p>
        )}

        {isLoading ? (
          <p className="text-label text-nb-faint">Reading this course…</p>
        ) : available.length === 0 ? (
          <p className="text-label text-nb-faint">
            No files for this course yet. Ingest a lecture and Argus will store it, index it, and
            write you a note from it.
          </p>
        ) : visible.length === 0 ? (
          <p className="text-label text-nb-faint">Nothing matches “{filter}”.</p>
        ) : (
          ZONES.map(({ key, label }) => {
            const rows = visible.filter((source) => source.zone === key);
            if (rows.length === 0) return null;
            return (
              <div key={key} className="mb-3 last:mb-0">
                <p className="mb-2 text-label text-nb-faint">
                  {label} · {rows.length}
                </p>
                <ul className="space-y-1.5">
                  {rows.map((source) => {
                    const fromHere = deckCounts.get(source.path) ?? 0;
                    return (
                    <li
                      key={source.path}
                      className={`rounded-tile transition-colors ${
                        selected.has(source.path) ? "bg-nb-raised" : "hover:bg-nb-raised"
                      }`}
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
                        className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left"
                      >
                        <span
                          aria-hidden
                          className={`mt-0.5 flex h-[0.9375rem] w-[0.9375rem] shrink-0 items-center justify-center rounded-[0.25rem] text-[0.625rem] transition-colors ${
                            selected.has(source.path)
                              ? "bg-[var(--ac)] text-nb-onAc"
                              : "border border-nb-lineHi"
                          }`}
                        >
                          {selected.has(source.path) && "✓"}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-ctl text-nb-ink">{source.title}</span>
                          {/* One line, not three badges. The kind is already in
                              the filename, and "2 decks came from this" says
                              what a bare "2 decks" chip only hinted at. */}
                          <span
                            className={`mt-0.5 block text-meta ${
                              source.chunks === null ? "text-warn" : "text-nb-faint"
                            }`}
                          >
                            {formatRelativeTime(source.modified)}
                            {source.chunks !== null &&
                              ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                            {source.chunks === null && " · not indexed yet"}
                            {fromHere > 0 &&
                              ` · ${fromHere} deck${fromHere === 1 ? "" : "s"} came from this`}
                          </span>
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
          <div className="mt-3.5 border-t border-nb-line pt-3.5">
            <p className="mb-2 text-label text-nb-faint">study · {excluded.length}</p>
            <ul className="flex flex-col gap-1">
              {excluded.map((source) => (
                <li key={source.path} className="truncate text-label text-nb-body">
                  {source.title}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-label text-nb-faint">
              Argus&apos;s own output — not used as a source.
            </p>
          </div>
        )}
      </NotebookPanel>

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
