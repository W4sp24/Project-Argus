"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useCourseSources, type CourseSource } from "@/lib/api";

/**
 * Which of a course's files the Course Hub is currently working from.
 *
 * The three panes are siblings — SOURCES ticks the boxes, ARGUS.CHAT sends
 * them on every frame, STUDIO sends them to the generators — so the selection
 * is a context rather than state inside the rail. Panel-local state would
 * have made the checkboxes exactly what they were before this: a control
 * nothing downstream can read.
 *
 * Persisted per course in `localStorage`, because a selection is a working
 * set: opening the same course tomorrow to keep working through the same
 * three lectures should not start by re-ticking them. It is reconciled
 * against the live file list on every change, so a deleted file drops out and
 * a newly ingested one arrives selected — a file that just finished ingesting
 * being silently excluded from the next question would be the worst possible
 * default.
 *
 * The rail's filter box lives here too, beside the selection rather than
 * inside the panel. `ALL`/`NONE` have to mean "all of what you can see", and
 * a bulk control that cannot see the filter can only mean "all of what you
 * can't" — which is how `ALL` used to tick 15 files while one row was on
 * screen, and persist all 15.
 *
 * `study` files are excluded on purpose: those are Argus's own output
 * (generated guides, exam markdown), and feeding them back in as sources is a
 * loop, not context.
 */

const STORAGE_PREFIX = "argus:course-sources:";

export interface CourseSelection {
  /** Files the rail lists: the `materials` and `notes` zones. */
  available: CourseSource[];
  /** `available` narrowed by `filter` — the rows actually on screen. */
  visible: CourseSource[];
  /** The rail's filter text, raw as typed (the panel echoes it back). */
  filter: string;
  setFilter: (value: string) => void;
  /** Whether `visible` is a real narrowing of `available`. */
  isFiltered: boolean;
  selected: Set<string>;
  /** `selected`, ordered and array-shaped, for sending over the wire. */
  paths: string[];
  toggle: (path: string) => void;
  /** Tick every *visible* row, leaving anything the filter hides alone. */
  selectAll: () => void;
  /** Untick every *visible* row, leaving anything the filter hides alone. */
  selectNone: () => void;
  /** The escape hatch: the whole course, filter or no filter. */
  selectAllInCourse: () => void;
  /** The escape hatch: clear the whole course, filter or no filter. */
  selectNoneInCourse: () => void;
  /** Refetch the file list — the ingest panel calls this when a job lands. */
  refresh: () => void;
  isLoading: boolean;
}

const SelectionContext = createContext<CourseSelection | null>(null);

function storageKey(code: string): string {
  return `${STORAGE_PREFIX}${code}`;
}

/** Read a stored selection, or `null` when there isn't one.
 *
 * `null` and `[]` are different answers: never chosen (default to everything)
 * versus deliberately cleared. Every access is wrapped because a private
 * window, or a browser set to block site data, throws rather than returning
 * empty. */
function readStored(code: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(storageKey(code));
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : null;
  } catch {
    return null;
  }
}

function writeStored(code: string, paths: string[]): void {
  try {
    window.localStorage.setItem(storageKey(code), JSON.stringify(paths));
  } catch {
    // A selection that cannot be remembered still has to work for this visit.
  }
}

export function CourseSelectionProvider({ code, children }: { code: string; children: ReactNode }) {
  const { data, isLoading, mutate } = useCourseSources(code);

  const available = useMemo(
    () => (data ?? []).filter((source) => source.zone === "materials" || source.zone === "notes"),
    [data],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  // Tracks which paths this hook has already made a decision about. A path
  // that is present here but absent from `available` was deleted; one absent
  // from here is new and gets selected. Without it, "unticked by the user"
  // and "not yet seen" are indistinguishable, and every reconcile would
  // re-tick everything the user had just cleared.
  const known = useRef<Set<string> | null>(null);
  // Which course `known` describes. This used to be a second effect keyed on
  // `[code]` that reset the ref and the selection — and it raced the
  // reconcile below, because a `setSelected` updater is *lazy*. The reconcile
  // queued an updater; the reset then queued a plain empty set; React drained
  // both, the updater ran first and populated `known` as a side effect, and
  // the plain empty set replaced its result. Every later reconcile then took
  // the "add only unseen paths" branch and found none, so a freshly opened
  // hub was pinned at nothing selected — the exact opposite of what this
  // module's contract promises.
  //
  // One effect now, and the first-reconcile branch runs in the effect *body*
  // rather than inside an updater, so the surviving updater is pure and there
  // is nothing left to race. Refs survive StrictMode's double-invoked
  // effects, so the second run takes the incremental branch with `seen`
  // already equal to every path, adds nothing, and is a no-op.
  const initialisedFor = useRef<string | null>(null);
  // Whether the user has deliberately changed this course's selection yet.
  // Persistence hangs off this rather than off each mutator, so every mutator
  // can be a pure `setSelected` updater -- which matters because a range
  // select calls `toggle` in a loop, and a mutator that read `selected` from
  // its own closure would drop every write but the last.
  //
  // It also keeps `readStored`'s contract intact: `null` (never chosen ->
  // default to everything) and `[]` (deliberately cleared) have to stay
  // different answers, so the default selection must NOT be written back.
  const persist = useRef(false);

  useEffect(() => {
    if (!data) return;
    const paths = available.map((source) => source.path);

    if (initialisedFor.current !== code) {
      initialisedFor.current = code;
      persist.current = false;
      const stored = readStored(code);
      known.current = new Set(paths);
      setSelected(new Set(stored === null ? paths : stored.filter((path) => paths.includes(path))));
      return;
    }

    const seen = known.current ?? new Set<string>();
    known.current = new Set(paths);
    setSelected((current) => {
      const next = new Set([...current].filter((path) => paths.includes(path)));
      for (const path of paths) {
        if (!seen.has(path)) next.add(path);
      }
      return next;
    });
  }, [available, code, data]);

  useEffect(() => {
    if (!persist.current) return;
    writeStored(code, [...selected]);
  }, [code, selected]);

  // Moving to a different course clears the filter: it described the previous
  // course's file names, and carrying it over would open the new hub already
  // hiding rows for a reason nothing on screen explains.
  useEffect(() => {
    setFilter("");
  }, [code]);

  const needle = filter.trim().toLowerCase();
  const visible = useMemo(
    () =>
      needle
        ? available.filter(
            (source) =>
              source.title.toLowerCase().includes(needle) ||
              source.path.toLowerCase().includes(needle),
          )
        : available,
    [available, needle],
  );
  const isFiltered = needle.length > 0 && visible.length < available.length;

  /** Mark this a deliberate change, so the effect above writes it through. */
  const commit = useCallback((next: (current: Set<string>) => Set<string>) => {
    persist.current = true;
    setSelected(next);
  }, []);

  const toggle = useCallback(
    (path: string) => {
      commit((current) => {
        const next = new Set(current);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      });
    },
    [commit],
  );

  // Scoped to `visible`, and a union rather than a replacement: a filtered
  // `ALL` must not silently untick the files the filter is hiding, the same
  // way it must not silently tick them.
  const selectAll = useCallback(
    () =>
      commit((current) => {
        const next = new Set(current);
        for (const source of visible) next.add(source.path);
        return next;
      }),
    [commit, visible],
  );

  const selectNone = useCallback(
    () =>
      commit((current) => {
        const next = new Set(current);
        for (const source of visible) next.delete(source.path);
        return next;
      }),
    [commit, visible],
  );

  const selectAllInCourse = useCallback(
    () => commit(() => new Set(available.map((source) => source.path))),
    [available, commit],
  );
  const selectNoneInCourse = useCallback(() => commit(() => new Set()), [commit]);
  const refresh = useCallback(() => void mutate(), [mutate]);

  const value = useMemo<CourseSelection>(
    () => ({
      available,
      visible,
      filter,
      setFilter,
      isFiltered,
      selected,
      // Ordered by the rail's own order rather than by insertion, so the same
      // selection always produces the same request.
      paths: available.map((source) => source.path).filter((path) => selected.has(path)),
      toggle,
      selectAll,
      selectNone,
      selectAllInCourse,
      selectNoneInCourse,
      refresh,
      isLoading,
    }),
    [
      available,
      visible,
      filter,
      isFiltered,
      selected,
      toggle,
      selectAll,
      selectNone,
      selectAllInCourse,
      selectNoneInCourse,
      refresh,
      isLoading,
    ],
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

/**
 * The current course's selection. Throws outside the provider rather than
 * returning a silent "everything" default — a pane that quietly searched the
 * whole vault because it was mounted in the wrong place is the failure this
 * whole change exists to remove.
 */
export function useCourseSelection(): CourseSelection {
  const value = useContext(SelectionContext);
  if (value === null) {
    throw new Error("useCourseSelection must be used inside a <CourseSelectionProvider>");
  }
  return value;
}
