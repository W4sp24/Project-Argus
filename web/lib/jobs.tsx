"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { useToast } from "@/components/Toast";
import { type IngestJob, useAllJobs } from "@/lib/api";
import { isRunning, jobsSignature, reconcile } from "@/lib/jobs/reducer";

/** The shape an optimistic entry takes before the server has said anything.
 *  Every field is what a just-created job is actually true of. */
const OPTIMISTIC_JOB: IngestJob = {
  id: "",
  created_at: "",
  finished_at: null,
  status: "queued",
  kind: "",
  params: null,
  target: "",
  summary_prompt: "",
  note_style: "",
  total: 0,
  done: 0,
  error: null,
};

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
  /** Start watching a job id returned by a 202. `optimistic` lets the caller say what kind and
   *  course it is, so `isBusy` can match on it before the first poll. */
  track: (id: string, optimistic?: Partial<IngestJob>) => void;
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

  /**
   * Jobs this window has tracked but has not yet seen in a poll.
   *
   * `isBusy` reads `running`, which is built from the last poll intersected
   * with `tracked` — so a job that was *just* tracked is in neither, and
   * every `disabled={busy(kind)}` button stayed live for up to one poll
   * interval after the click that started the job. That is the whole
   * double-click window, and it is wide enough to hit by accident: ~900ms
   * once the interval flips, longer if the flip lands late.
   *
   * The optimistic entry carries the kind and params the caller already
   * knows, so a Course Hub button can match on its own course rather than
   * merely on "something is running". It is dropped as soon as the server
   * reports the job, and after a grace period if the server never does.
   */
  const [pending, setPending] = useState<IngestJob[]>([]);

  const track = useCallback((id: string, optimistic?: Partial<IngestJob>) => {
    setTracked((current) => (current.includes(id) ? current : [...current, id]));
    setPending((current) =>
      current.some((job) => job.id === id)
        ? current
        : [...current, { ...OPTIMISTIC_JOB, ...optimistic, id }],
    );
    // A job the server never reports -- pruned, or a 202 for a row that
    // failed immediately -- would otherwise leave its button disabled for the
    // life of the window. Two idle polls is long enough that this never
    // races a slow first poll.
    window.setTimeout(
      () => setPending((current) => current.filter((job) => job.id !== id)),
      IDLE_POLL_MS * 2,
    );
  }, []);

  // Drop an optimistic entry once the server has actually reported the job,
  // so its real status (including "already finished") takes over.
  useEffect(() => {
    if (!data || pending.length === 0) return;
    const known = new Set(data.jobs.map((job) => job.id));
    const stillPending = pending.filter((job) => !known.has(job.id));
    if (stillPending.length !== pending.length) setPending(stillPending);
  }, [data, pending]);

  // SWR reparses the response on every poll, so `data` changes identity every
  // 900ms while anything is in flight even when the backend said the same
  // thing. Holding the previous array whenever the signature matches is what
  // stops that from re-rendering JobTray, GenerateDialog, CourseHub and
  // CoursesPanel on a timer.
  const stable = useRef<IngestJob[]>([]);
  const running = useMemo(() => {
    const seen = (data?.jobs ?? []).filter(
      (job) => tracked.includes(job.id) && isRunning(job.status),
    );
    const known = new Set(seen.map((job) => job.id));
    const next = [...seen, ...pending.filter((job) => !known.has(job.id))];
    if (jobsSignature(next) === jobsSignature(stable.current)) return stable.current;
    stable.current = next;
    return next;
  }, [data, tracked, pending]);

  const isBusy = useCallback(
    (predicate: (job: IngestJob) => boolean) => running.some(predicate),
    // `running` now only changes identity when its contents do, so depending on
    // it here no longer rebuilds this callback on every poll.
    [running],
  );

  // Without this the value object was rebuilt on every render — including every
  // poll tick — re-rendering every consumer whether or not the jobs moved.
  const value = useMemo(() => ({ jobs: running, track, isBusy }), [running, track, isBusy]);

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}
