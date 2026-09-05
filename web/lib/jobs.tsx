"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useToast } from "@/components/Toast";
import { type IngestJob, useAllJobs } from "@/lib/api";
import { isRunning, reconcile } from "@/lib/jobs/reducer";

/**
 * Long-running work, owned above the router.
 *
 * Study generation takes minutes. It used to be a `fetch` held open inside
 * `CourseHub`, with its only progress state in a `useState` — so navigating to
 * another tab unmounted the one record the UI had of work the backend was
 * still doing. The guide still landed in the vault; nothing ever said where.
 * `backend/features/study/jobs.py` was written to fix exactly that, and its
 * `background: true` path was never called by anything.
 *
 * Two things make this the fix rather than a nicer spinner:
 *
 *   1. It is mounted in `(dashboard)/layout.tsx`, above every route, so no
 *      navigation can unmount it.
 *   2. It rebuilds its tracked set from `GET /api/ingest/jobs?kind=all`, so
 *      recovery survives a reload, a crash, and a second window — all of which
 *      share one backend. A `localStorage` copy would disagree with that
 *      backend the moment either window acted.
 *
 * Every decision about *which* jobs matter lives in `jobs/reducer.ts`, which
 * is pure and unit-tested. What is left here is IO and React state.
 */

const IDLE_POLL_MS = 4000;
const ACTIVE_POLL_MS = 900;

interface JobsState {
  /** Every job currently in flight, whatever started it. */
  jobs: IngestJob[];
  /** Follow a job id returned by a 202. */
  track: (id: string) => void;
  /** Is some in-flight job matching `predicate`? Replaces per-component busy flags. */
  isBusy: (predicate: (job: IngestJob) => boolean) => boolean;
}

const JobsContext = createContext<JobsState | null>(null);

export function useJobs(): JobsState {
  const state = useContext(JobsContext);
  if (!state) throw new Error("useJobs must be used inside <JobsProvider>");
  return state;
}

/** How a job names itself in the tray and in its completion toast. */
export const KIND_LABEL: Record<string, string> = {
  ingest: "ingest",
  reindex: "reindex",
  relink: "relink",
  guide: "study guide",
  exam: "practice exam",
  deck: "flashcard deck",
};

export function labelFor(job: IngestJob): string {
  return KIND_LABEL[job.kind] ?? job.kind;
}

/** What a finished job says. The written path is the whole point of saying anything. */
function summarise(job: IngestJob): string {
  const params = job.params ?? {};
  const path = typeof params.path === "string" ? params.path : null;
  if (job.status === "failed") {
    return `${labelFor(job)} failed :: ${job.error ?? "no reason recorded"}`;
  }
  return path ? `${labelFor(job)} ready :: ${path}` : `${labelFor(job)} ready`;
}

export function JobsProvider({ children }: { children: ReactNode }) {
  const { show } = useToast();
  const [tracked, setTracked] = useState<string[]>([]);
  // Poll fast while something is in flight, slowly when idle. The idle poll is
  // what adopts a job started from the *other* window.
  const { data } = useAllJobs(tracked.length > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS);

  // Announcing from inside the reconcile effect alone would re-toast on any
  // re-render that produced the same finished job. This makes each id announce
  // exactly once for the life of the window.
  const announced = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!data) return;
    const { tracked: next, finished } = reconcile(tracked, data.jobs);
    for (const job of finished) {
      if (announced.current.has(job.id)) continue;
      announced.current.add(job.id);
      show(summarise(job), job.status === "failed" ? { tone: "error" } : undefined);
    }
    // Only write when the set actually changed: setState with an equal array
    // still re-renders, and this effect depends on `tracked`.
    if (next.length !== tracked.length || next.some((id, index) => id !== tracked[index])) {
      setTracked(next);
    }
  }, [data, tracked, show]);

  const track = useCallback((id: string) => {
    setTracked((current) => (current.includes(id) ? current : [...current, id]));
  }, []);

  const running = (data?.jobs ?? []).filter(
    (job) => tracked.includes(job.id) && isRunning(job.status),
  );

  const isBusy = useCallback(
    (predicate: (job: IngestJob) => boolean) => running.some(predicate),
    // `running` is rebuilt each render from `data`/`tracked`; depending on its
    // identity is correct and cheap here — the list is at most a few entries.
    [running],
  );

  return (
    <JobsContext.Provider value={{ jobs: running, track, isBusy }}>{children}</JobsContext.Provider>
  );
}
