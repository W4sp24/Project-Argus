/**
 * The whole decision surface of the job registry, kept pure.
 *
 * `JobsProvider` does IO and holds React state; every question about *which*
 * jobs matter is answered here. That split is deliberate: it keeps the
 * provider thin enough that its Playwright coverage is honest rather than a
 * stand-in for logic nothing ever exercised directly.
 */

export interface JobLike {
  id: string;
  status: string;
  kind: string;
  params: Record<string, unknown> | null;
}

/** The statuses `store.finish_job` can leave behind. Anything else is in flight. */
const TERMINAL = new Set(["ok", "partial", "failed"]);

export function isRunning(status: string): boolean {
  return !TERMINAL.has(status);
}

/**
 * Fold a fresh server listing into the tracked set.
 *
 * - A tracked job that reached a terminal status is reported once in
 *   `finished`, then dropped.
 * - A running job nobody was tracking is adopted. This is the recovery path,
 *   and it is why the server listing is the source of truth rather than
 *   localStorage: two windows share one backend, and a stale local copy would
 *   disagree with it the moment either window acted.
 * - A tracked id with no row is dropped rather than held pending forever.
 */
export function reconcile<T extends JobLike>(
  tracked: string[],
  jobs: T[],
): { tracked: string[]; finished: T[] } {
  // Generic rather than taking `JobLike[]`: callers pass the full `IngestJob`,
  // and a non-generic signature would narrow `finished` to the structural
  // subset, losing the `target` and `error` a completion message needs.
  const byId = new Map(jobs.map((job) => [job.id, job]));
  const next: string[] = [];
  const finished: T[] = [];

  for (const id of tracked) {
    const job = byId.get(id);
    if (job === undefined) continue; // vanished -- do not hold it pending
    if (isRunning(job.status)) next.push(id);
    else finished.push(job);
  }

  const known = new Set(tracked);
  for (const job of jobs) {
    if (!known.has(job.id) && isRunning(job.status)) next.push(job.id);
  }

  return { tracked: next, finished };
}

/**
 * A value signature for a list of jobs: the fields the UI actually renders.
 *
 * SWR hands back a freshly parsed object on every revalidation, so the derived
 * "running jobs" array changed identity on every poll tick — every 900ms while
 * anything is in flight — even when the backend said exactly the same thing.
 * `JobsProvider` compares this instead, and keeps the previous array when it
 * matches, so its context value stops re-rendering every consumer for nothing.
 *
 * Deliberately not `JSON.stringify(jobs)`: that is key-order dependent and
 * would fold in fields no consumer reads, making the signature change (and so
 * the re-render happen) for reasons the UI cannot show.
 */
export function jobsSignature(jobs: JobLike[]): string {
  return jobs.map((job) => `${job.id}:${job.status}:${job.kind}`).join("|");
}
