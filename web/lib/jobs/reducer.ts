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
export function reconcile(
  tracked: string[],
  jobs: JobLike[],
): { tracked: string[]; finished: JobLike[] } {
  const byId = new Map(jobs.map((job) => [job.id, job]));
  const next: string[] = [];
  const finished: JobLike[] = [];

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
