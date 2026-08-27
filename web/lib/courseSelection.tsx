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
 * `study` files are excluded on purpose: those are Argus's own output
 * (generated guides, exam markdown), and feeding them back in as sources is a
 * loop, not context.
 */

const STORAGE_PREFIX = "argus:course-sources:";

export interface CourseSelection {
  /** Files the rail lists: the `materials` and `notes` zones. */
  available: CourseSource[];
  selected: Set<string>;
  /** `selected`, ordered and array-shaped, for sending over the wire. */
  paths: string[];
  toggle: (path: string) => void;
  selectAll: () => void;
  selectNone: () => void;
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
  // Tracks which paths this hook has already made a decision about. A path
  // that is present here but absent from `available` was deleted; one absent
  // from here is new and gets selected. Without it, "unticked by the user"
  // and "not yet seen" are indistinguishable, and every reconcile would
  // re-tick everything the user had just cleared.
  const known = useRef<Set<string> | null>(null);

  useEffect(() => {
    if (!data) return;
    const paths = available.map((source) => source.path);
    setSelected((current) => {
      if (known.current === null) {
        const stored = readStored(code);
        known.current = new Set(paths);
        return new Set(stored === null ? paths : stored.filter((path) => paths.includes(path)));
      }
      const next = new Set([...current].filter((path) => paths.includes(path)));
      for (const path of paths) {
        if (!known.current.has(path)) next.add(path);
      }
      known.current = new Set(paths);
      return next;
    });
  }, [available, code, data]);

  // Reset when the hub moves to a different course: the ref would otherwise
  // carry the previous course's paths into the new one's first reconcile.
  useEffect(() => {
    known.current = null;
    setSelected(new Set());
  }, [code]);

  const update = useCallback(
    (next: Set<string>) => {
      setSelected(next);
      writeStored(code, [...next]);
    },
    [code],
  );

  const toggle = useCallback(
    (path: string) => {
      setSelected((current) => {
        const next = new Set(current);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        writeStored(code, [...next]);
        return next;
      });
    },
    [code],
  );

  const selectAll = useCallback(
    () => update(new Set(available.map((source) => source.path))),
    [available, update],
  );
  const selectNone = useCallback(() => update(new Set()), [update]);
  const refresh = useCallback(() => void mutate(), [mutate]);

  const value = useMemo<CourseSelection>(
    () => ({
      available,
      selected,
      // Ordered by the rail's own order rather than by insertion, so the same
      // selection always produces the same request.
      paths: available.map((source) => source.path).filter((path) => selected.has(path)),
      toggle,
      selectAll,
      selectNone,
      refresh,
      isLoading,
    }),
    [available, selected, toggle, selectAll, selectNone, refresh, isLoading],
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
