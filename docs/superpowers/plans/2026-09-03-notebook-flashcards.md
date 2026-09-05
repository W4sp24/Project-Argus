# Notebook & Flashcards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long generations survive navigation, move Study into its own OS window under the name Notebook, and rebuild flashcards into an authorable deck with four study activities.

**Architecture:** Three phases, each leaving the app working. Phase 1 moves job ownership from a React component local into a provider above the router and opts both generation callsites into the backend's existing `background: true` path. Phase 2 renames the Study mode to Notebook and teaches the Electron shell to open exactly one extra window for it. Phase 3 turns `flashcard_decks.cards_json` into real `flashcard_cards` rows without touching a single review, then builds authoring and four activities on top.

**Tech Stack:** FastAPI + SQLite (`backend/`), Next.js 14 App Router + SWR + Tailwind (`web/`), Electron (`desktop/`), pytest, Playwright, and Vitest (added in Task 1).

**Spec:** `docs/superpowers/specs/2026-09-03-notebook-flashcards-design.md`

## Global Constraints

- **Branch:** `feature/notebook-flashcards`, cut from `origin/main` at `df513b5`. Conventional commits. Do not bump any version — releasing is a separate act, and `desktop/scripts/check-versions.mjs` only enforces agreement on tags.
- **Python is `.venv`'s.** Run `.venv/Scripts/python -m pytest`, never bare `pytest` — bare collection fails on this machine.
- **`ruff check .` is repo-wide with no allow-list** and must stay clean.
- **All API calls go through `apiFetch`/`mutateJSON`/`fetcher` in `web/lib/api.ts`.** A bare `fetch("/api/...")` works in dev and 404s when packaged, because Next bakes rewrites at build time and the packaged backend port is chosen at launch.
- **Type sizes come from the named scale** (`text-micro`/`meta`/`label`/`body`/`lead`/`title`/`display`). Never `text-[13px]`.
- **Never write `focus:outline-none`** — Tailwind emits it at specificity (0,2,0) and it beats the bare `:focus-visible` rule in `globals.css`.
- **No `window.confirm`/`window.prompt`.** Use `useConfirm`/`ConfirmDialog` from `web/components/ui/`.
- **Overlays use `web/components/ui/Dialog.tsx`.** It is the only overlay implementation (focus trap, Escape stack, refcounted scroll lock).
- **e2e fixtures are built inside the test**, never seeded at backend startup. The suite runs `workers: 1` against one shared vault; a startup seed is global state that has broken this suite before.
- **The e2e suite is coupled to visible text, uppercase accessible names, and the `▍` glyph from `Panel.tsx`.** Change classes freely; preserve those.
- **Invariant I1:** vault writes go through `backend/vault/writer.py` and take a git snapshot. The one sanctioned exception is a course's `study/` folder.
- **Card content is Markdown+LaTeX** and renders through `web/components/Markdown.tsx`. Card faces must never carry an `aria-label` — it overrides descendant content and makes a screen reader read LaTeX source instead of KaTeX's MathML.

---

# Phase 1 — Generation survives navigation

## Task 1: Vitest harness and the job-tracking reducer

The reducer is pure on purpose: it holds all of the provider's decision-making, so the provider itself stays thin enough that Playwright is honest coverage for it. Vitest runs in `node` environment — no DOM, no `@testing-library`, one new dependency.

**Files:**
- Create: `web/vitest.config.ts`
- Create: `web/lib/jobs/reducer.ts`
- Create: `web/lib/jobs/reducer.test.ts`
- Modify: `web/package.json` (devDependency + `test:unit` script)
- Modify: `.github/workflows/_test.yml` (one step in the existing `web` job)

**Interfaces:**
- Consumes: nothing.
- Produces: `TrackedJob = { id: string; kind: string }`, and
  `reconcile(tracked: string[], jobs: JobLike[]): { tracked: string[]; finished: JobLike[] }`
  where `JobLike = { id: string; status: string; kind: string; params: Record<string, unknown> | null }`.

- [ ] **Step 1: Install Vitest**

```bash
cd web && npm install --save-dev vitest@^2.1.8
```

- [ ] **Step 2: Add the config**

Create `web/vitest.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vitest/config";

/**
 * Node environment, no DOM. Everything unit-tested here is pure logic —
 * answer matching, distractor selection, delimited parsing, job
 * reconciliation. Components are covered by Playwright against a real
 * backend, which is the only place their SWR + WebSocket behaviour is real.
 */
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname) } },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
```

- [ ] **Step 3: Add the script**

In `web/package.json`, add to `scripts`:

```json
"test:unit": "vitest run"
```

- [ ] **Step 4: Write the failing test**

Create `web/lib/jobs/reducer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { reconcile, type JobLike } from "./reducer";

const job = (id: string, status: string, kind = "guide"): JobLike => ({
  id,
  status,
  kind,
  params: null,
});

describe("reconcile", () => {
  it("keeps tracking a job that is still running", () => {
    const out = reconcile(["a"], [job("a", "running")]);
    expect(out.tracked).toEqual(["a"]);
    expect(out.finished).toEqual([]);
  });

  it("reports a job that reached a terminal status, and stops tracking it", () => {
    const out = reconcile(["a"], [job("a", "ok")]);
    expect(out.tracked).toEqual([]);
    expect(out.finished.map((j) => j.id)).toEqual(["a"]);
  });

  it("treats partial and failed as terminal too", () => {
    expect(reconcile(["a"], [job("a", "partial")]).finished).toHaveLength(1);
    expect(reconcile(["a"], [job("a", "failed")]).finished).toHaveLength(1);
  });

  it("adopts a running job it was not tracking", () => {
    // This is recovery: a reload, a crash, or a second window opening
    // mid-job. The server is the source of truth, not localStorage.
    const out = reconcile([], [job("b", "queued")]);
    expect(out.tracked).toEqual(["b"]);
  });

  it("does not adopt a job that already finished", () => {
    // Otherwise every mount would re-announce the whole history.
    expect(reconcile([], [job("b", "ok")]).tracked).toEqual([]);
    expect(reconcile([], [job("b", "ok")]).finished).toEqual([]);
  });

  it("drops a tracked id the server no longer knows about", () => {
    // A vanished row cannot finish, so holding its id would leak a
    // permanently-pending entry into the tray.
    expect(reconcile(["gone"], []).tracked).toEqual([]);
  });

  it("preserves tracking order and does not duplicate on re-adoption", () => {
    const out = reconcile(["a"], [job("a", "running"), job("b", "running")]);
    expect(out.tracked).toEqual(["a", "b"]);
  });
});
```

- [ ] **Step 5: Run it and watch it fail**

Run: `cd web && npm run test:unit`
Expected: FAIL — `Failed to resolve import "./reducer"`.

- [ ] **Step 6: Write the reducer**

Create `web/lib/jobs/reducer.ts`:

```ts
/**
 * The whole decision surface of the job registry, kept pure.
 *
 * `JobsProvider` does IO and holds React state; every question about *which*
 * jobs matter is answered here, so the provider stays thin enough that its
 * Playwright coverage is honest rather than a stand-in for untested logic.
 */

export interface JobLike {
  id: string;
  status: string;
  kind: string;
  params: Record<string, unknown> | null;
}

/** Statuses `store.finish_job` can leave behind. Anything else is in flight. */
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
 *   disagree with it the moment either one acts.
 * - A tracked id with no row is dropped rather than kept pending forever.
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
    if (job === undefined) continue; // vanished — do not hold it pending
    if (isRunning(job.status)) next.push(id);
    else finished.push(job);
  }

  const known = new Set(tracked);
  for (const job of jobs) {
    if (!known.has(job.id) && isRunning(job.status)) next.push(job.id);
  }

  return { tracked: next, finished };
}
```

- [ ] **Step 7: Run it and watch it pass**

Run: `cd web && npm run test:unit`
Expected: PASS, 7 tests.

- [ ] **Step 8: Add the CI step**

In `.github/workflows/_test.yml`, in the `web` job, between the `npm run lint` and `npm run build` steps:

```yaml
      - run: npm run test:unit
        working-directory: web
```

- [ ] **Step 9: Commit**

```bash
git add web/vitest.config.ts web/lib/jobs/reducer.ts web/lib/jobs/reducer.test.ts \
        web/package.json web/package-lock.json .github/workflows/_test.yml
git commit -m "test(web): a unit runner, and the job registry's decision logic"
```

---

## Task 2: The job registry and its tray

**Files:**
- Create: `web/lib/jobs.tsx`
- Create: `web/components/JobTray.tsx`
- Modify: `web/lib/api.ts` (add `kind`/`params` to `IngestJob`; add `useAllJobs`)
- Modify: `web/app/(dashboard)/layout.tsx`
- Modify: `web/components/TopBar.tsx`

**Interfaces:**
- Consumes: `reconcile`, `isRunning`, `JobLike` from Task 1; `useIngestJob` (`web/lib/api.ts:1145`).
- Produces: `<JobsProvider>`, and
  `useJobs(): { jobs: IngestJob[]; track: (id: string) => void; isBusy: (predicate: (job: IngestJob) => boolean) => boolean }`.

- [ ] **Step 1: Complete the `IngestJob` type**

The backend has returned `kind` and `params` since the job store was generalised (`backend/features/ingest/store.py:111` `_job_row`), but the TypeScript interface never gained them. In `web/lib/api.ts`, inside `export interface IngestJob`, after `status`:

```ts
  /** Which feature queued this: "ingest" | "reindex" | "relink" | "guide" | "exam" | "deck". */
  kind: string;
  /** The request's inputs, plus results with no column of their own —
   * a guide's `path`, an exam's `exam_id`, a deck's `deck_id`. */
  params: Record<string, unknown> | null;
```

- [ ] **Step 2: Add the all-kinds listing hook**

`GET /api/ingest/jobs` defaults to `kind=ingest`; `kind=all` is the documented way to get every kind (`backend/features/ingest/router.py:836`). Add beside `useIngestJobs` in `web/lib/api.ts`:

```ts
/**
 * Every job of every kind, newest first. `useIngestJobs()` is the ingest
 * history panel's narrower view; this one backs the global registry, which
 * must see guides and exams too.
 */
export function useAllJobs(refreshMs: number) {
  return useSWR<{ jobs: IngestJob[] }>("/api/ingest/jobs?kind=all", fetcher, {
    refreshInterval: refreshMs,
  });
}
```

- [ ] **Step 3: Write the provider**

Create `web/lib/jobs.tsx`:

```tsx
"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useToast } from "@/components/Toast";
import { useAllJobs, type IngestJob } from "@/lib/api";
import { isRunning, reconcile } from "@/lib/jobs/reducer";

/**
 * Long-running work, owned above the router.
 *
 * Study generation takes minutes. It used to be a `fetch` held open inside
 * `CourseHub`, with its only progress state in a `useState` — so navigating to
 * another tab unmounted the one record the UI had. The backend kept going and
 * still wrote the guide into the vault; nothing ever said where it landed.
 * `backend/features/study/jobs.py` was written to fix exactly that and its
 * `background: true` path was never called.
 *
 * Two things make this provider the fix rather than a nicer spinner:
 *
 *   1. It is mounted in `(dashboard)/layout.tsx`, above every route, so no
 *      navigation can unmount it.
 *   2. It rebuilds its tracked set from `GET /api/ingest/jobs?kind=all` on
 *      mount and on focus, so recovery survives a reload, a crash, and a
 *      second window — all of which share one backend. A `localStorage` copy
 *      would disagree with that backend the moment either window acted.
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

/** How a finished job describes itself in the completion toast. */
function summarise(job: IngestJob): string {
  const params = job.params ?? {};
  const path = typeof params.path === "string" ? params.path : null;
  if (job.status === "failed") return `${job.kind} failed :: ${job.error ?? "no reason recorded"}`;
  if (path) return `${job.kind} ready :: ${path}`;
  return `${job.kind} ready`;
}

export function JobsProvider({ children }: { children: ReactNode }) {
  const { show } = useToast();
  const [tracked, setTracked] = useState<string[]>([]);
  // Poll fast while something is in flight, slowly when idle. The idle poll is
  // what adopts a job started from the *other* window.
  const { data } = useAllJobs(tracked.length > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS);
  const jobs = data?.jobs ?? [];

  // Announcing inside the reconcile effect would re-toast on every re-render
  // that produced the same finished job; this makes each id announce once.
  const announced = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!data) return;
    const { tracked: next, finished } = reconcile(tracked, data.jobs);
    for (const job of finished) {
      if (announced.current.has(job.id)) continue;
      announced.current.add(job.id);
      show(summarise(job), job.status === "failed" ? { tone: "error" } : undefined);
    }
    // Only write when the set actually changed — setState with an equal array
    // still re-renders, and this effect depends on `tracked`.
    if (next.length !== tracked.length || next.some((id, i) => id !== tracked[i])) {
      setTracked(next);
    }
  }, [data, tracked, show]);

  const track = useCallback((id: string) => {
    setTracked((current) => (current.includes(id) ? current : [...current, id]));
  }, []);

  const running = jobs.filter((job) => tracked.includes(job.id) && isRunning(job.status));

  const isBusy = useCallback(
    (predicate: (job: IngestJob) => boolean) => running.some(predicate),
    // `running` is rebuilt each render from `jobs`/`tracked`; depending on its
    // identity is correct here and cheap — the list is at most a few entries.
    [running],
  );

  return (
    <JobsContext.Provider value={{ jobs: running, track, isBusy }}>{children}</JobsContext.Provider>
  );
}
```

- [ ] **Step 4: Write the tray**

Create `web/components/JobTray.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useJobs } from "@/lib/jobs";

/** Human label per job kind. `kind` is the wire value from `ingest_jobs.kind`. */
const KIND_LABEL: Record<string, string> = {
  ingest: "ingest",
  reindex: "reindex",
  relink: "relink",
  guide: "study guide",
  exam: "practice exam",
  deck: "flashcard deck",
};

/**
 * What is running right now, anywhere in the app.
 *
 * Renders nothing when nothing runs — this sits in the top bar and must not
 * cost a row of chrome to say "idle". The blinking bar is the same idiom
 * `IngestJobProgress` and `StudioAction` use, and for the same reason: it
 * moves only while something is actually running, so a stall looks like a
 * stall rather than like progress.
 */
export default function JobTray() {
  const { jobs } = useJobs();
  const [open, setOpen] = useState(false);

  if (jobs.length === 0) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`Background work: ${jobs.length} running`}
        className="flex items-center gap-2 border border-line px-2 py-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
      >
        <span className="h-1.5 w-1.5 animate-blink bg-[var(--ac)]" aria-hidden />
        {jobs.length} running
      </button>

      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-72 border border-line bg-sunken p-3">
          <ul className="space-y-2">
            {jobs.map((job) => (
              <li key={job.id}>
                <p className="font-mono text-meta uppercase tracking-[0.12em] text-ink">
                  {KIND_LABEL[job.kind] ?? job.kind}
                </p>
                <p className="truncate text-label text-ink-faint">{job.target}</p>
                <div className="mt-1 h-0.5 w-full bg-line" aria-hidden>
                  <span className="block h-full w-1/3 animate-blink bg-[var(--ac)]" />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Mount both**

In `web/app/(dashboard)/layout.tsx`, import `JobsProvider` from `@/lib/jobs` and nest it directly inside `ToastProvider` (it calls `useToast`) and outside `ModeProvider`:

```tsx
      <ToastProvider>
        <JobsProvider>
          <ModeProvider>
```

…with the matching closing tag. In `web/components/TopBar.tsx`, import `JobTray` and render `<JobTray />` in the right-hand control cluster, immediately before the engine-picker trigger.

- [ ] **Step 6: Verify it compiles and lints**

Run: `cd web && npx tsc --noEmit && npm run lint`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add web/lib/jobs.tsx web/components/JobTray.tsx web/lib/api.ts \
        "web/app/(dashboard)/layout.tsx" web/components/TopBar.tsx
git commit -m "feat(jobs): background work is owned above the router, not by a page"
```

---

## Task 3: Both generators go background, and the false mutex dies

`disabled={busyAction !== null}` is a single global flag: one running guide disables the exam button, the deck button and the upload button, for every course. Nothing in the backend requires it — `backend/features/study/router.py:286` states that study generation takes no single-flight slot and "contends with nothing". This is the reported inability to multitask.

**Files:**
- Modify: `web/components/study/CourseHub.tsx:222-295`
- Modify: `web/components/study/CoursesPanel.tsx:97,126-165,333-352`
- Modify: `web/e2e/study.spec.ts`

**Interfaces:**
- Consumes: `useJobs()` from Task 2.
- Produces: nothing new.

- [ ] **Step 1: Write the failing e2e test**

Append to `web/e2e/study.spec.ts`:

```ts
test("a generation started in the course hub survives leaving the tab", async ({ page }) => {
  // The reported bug: generation state lived in `busyAction`, a component
  // local, so navigating away discarded the only record the UI had of work
  // the backend was still doing.
  await page.goto("/study");
  await page.getByRole("link", { name: /CS000/ }).first().click();
  await expect(page).toHaveURL(/\/study\/course\/CS000/);

  const studio = page.getByRole("region", { name: /STUDIO/ });
  await studio.getByRole("button", { name: /study guide/ }).click();

  // The tray is the proof the job is owned above the router.
  const tray = page.getByRole("button", { name: /Background work/ });
  await expect(tray).toBeVisible();

  await page.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(page).toHaveURL(/\/study\/flashcards$/);
  await expect(tray).toBeVisible();

  await page.goto("/dashboard");
  await expect(tray).toBeVisible();
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && npx playwright test study.spec.ts -g "survives leaving the tab"`
Expected: FAIL — no `Background work` control, because the request is still synchronous.

- [ ] **Step 3: Rewrite `CourseStudio`'s generation**

In `web/components/study/CourseHub.tsx`, replace the `busyAction` state and the three generate functions:

```tsx
  const { track, isBusy } = useJobs();

  /** Is a job of this kind running for this course? Replaces the global flag. */
  const busy = (kind: string) =>
    isBusy((job) => job.kind === kind && job.params?.course === code);

  async function startGeneration(kind: "guide" | "exam") {
    const endpoint = kind === "guide" ? "/api/study/guide" : "/api/study/exam";
    try {
      const { job_id } = await mutateJSON<{ job_id: string }>(endpoint, {
        course: code,
        model: selectedModel(),
        sources: paths,
        // The backend has accepted this since the job store was generalised
        // (router.py:315). Nothing ever sent it, which is the whole bug.
        background: true,
        ...(kind === "exam" ? { n: 10 } : {}),
      });
      track(job_id);
      show(`${kind} queued — it keeps running if you leave this tab`);
    } catch (error) {
      show(`${kind} could not be queued: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    }
  }
```

Then each `StudioAction` takes `running={busy("guide")}` and `disabled={busy("guide") || nothingSelected}` — its **own** kind only, never the others'.

- [ ] **Step 4: Do the same in `CoursesPanel`**

`web/components/study/CoursesPanel.tsx`'s `generate(kind, course)` gets the identical `background: true` + `track()` treatment. Its `disabled` props become `busy(kind, course.code)`, so a guide running for CS201 no longer disables CS301's buttons. The upload button keeps its own local flag — an upload is a held-open request by nature and is not a job.

- [ ] **Step 5: Refresh the artifact lists when a job lands**

`CourseStudio` currently calls `refreshSources()` in the resolved branch of a promise that no longer exists. Replace with an effect keyed on the count of in-flight jobs for this course, so the GENERATED list reloads on the transition to zero:

```tsx
  const inFlight = jobs.filter((job) => job.params?.course === code).length;
  useEffect(() => {
    if (inFlight === 0) {
      refreshSources();
      refreshExams();
      refreshDecks();
      refreshSelection();
    }
  }, [inFlight, refreshSources, refreshExams, refreshDecks, refreshSelection]);
```

- [ ] **Step 6: Run the test and watch it pass**

Run: `cd web && npx playwright test study.spec.ts`
Expected: PASS, including the pre-existing cases.

- [ ] **Step 7: Commit**

```bash
git add web/components/study/CourseHub.tsx web/components/study/CoursesPanel.tsx web/e2e/study.spec.ts
git commit -m "fix(study): generation survives a tab switch, and no longer blocks itself"
```

---

# Phase 2 — Notebook, in its own window

## Task 4: Rename Study to Notebook

Mechanical but wide. Do it as one commit so no intermediate state has half the app on each name.

**Files:**
- Move: `web/app/(dashboard)/study/` → `web/app/(dashboard)/notebook/`
- Move: `web/components/study/` → `web/components/notebook/`
- Modify: `web/lib/mode.tsx`, `web/components/TopBar.tsx`, `web/next.config.mjs`
- Rename: `web/e2e/study.spec.ts` → `web/e2e/notebook.spec.ts`
- Modify: any file importing `@/components/study/*` (`grep -rl "components/study"`)

**Interfaces:**
- Consumes: nothing.
- Produces: `Mode` union member `"notebook"`; route prefix `/notebook`.

- [ ] **Step 1: Write the failing redirect test**

In `web/e2e/notebook.spec.ts` (after the rename in Step 2, write this first as a new case):

```ts
test("the old study URLs still land on the notebook", async ({ page }) => {
  await page.goto("/study");
  await expect(page).toHaveURL(/\/notebook$/);

  await page.goto("/study/flashcards");
  await expect(page).toHaveURL(/\/notebook\/flashcards$/);
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
```

- [ ] **Step 2: Move the directories with git, preserving history**

```bash
git mv "web/app/(dashboard)/study" "web/app/(dashboard)/notebook"
git mv web/components/study web/components/notebook
git mv web/e2e/study.spec.ts web/e2e/notebook.spec.ts
```

- [ ] **Step 3: Update the mode system**

In `web/lib/mode.tsx`: the `Mode` union member `"study"` → `"notebook"`; the `ACCENTS` key (keep the same cyan `{ ac: "#22d3ee", acBg: "#0c1a20" }` — the accent is the mode's identity and users recognise it); `MODE_ROUTES.notebook = "/notebook"`; and in `modeFromPathname`, `if (pathname.startsWith("/notebook")) return "notebook";`.

The `STORAGE_KEY` value stays `"argus-mode"` — it is written but never read back (reading would race the pathname-derived mode), so a stale `"study"` in localStorage is inert.

- [ ] **Step 4: Update every reference**

```bash
grep -rl "components/study" web --include="*.tsx" --include="*.ts" | grep -v node_modules
```

Rewrite those imports to `@/components/notebook/…`. In `web/components/TopBar.tsx` change the tab label to `NOTEBOOK` and its two-letter narrow form to `NB`. Rename `StudyTabs.tsx` → `NotebookTabs.tsx`, `StudyStatusLine.tsx` → `NotebookStatusLine.tsx`, and update the three hrefs in `TABS` to `/notebook`, `/notebook/flashcards`, `/notebook/exam`. Update every `/study` string in `notebook.spec.ts` and in `web/e2e/responsive.spec.ts`.

- [ ] **Step 5: Add the redirects**

In `web/next.config.mjs`, add to `nextConfig` beside `headers()`:

```js
  /**
   * Study became Notebook. Unlike `rewrites()` these carry no runtime port —
   * they are same-origin — so baking them at build time is correct, and they
   * keep every existing bookmark, obsidian:// deep link and note reference
   * working.
   */
  async redirects() {
    return [
      { source: "/study", destination: "/notebook", permanent: true },
      { source: "/study/:path*", destination: "/notebook/:path*", permanent: true },
    ];
  },
```

- [ ] **Step 6: Verify**

Run: `cd web && npx tsc --noEmit && npm run lint && npm run build && npx playwright test notebook.spec.ts`
Expected: all clean, all pass.

- [ ] **Step 7: Commit**

```bash
git add -A web/
git commit -m "refactor(notebook): study becomes notebook, and old links still work"
```

---

## Task 5: Standalone window chrome in the web app

**Files:**
- Create: `web/lib/standalone.ts`, `web/lib/standalone.test.ts`
- Create: `web/components/notebook/PopOutButton.tsx`
- Modify: `web/components/TopBar.tsx`, `web/components/notebook/NotebookStatusLine.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `resolveStandalone(search: string, stored: string | null): string | null`, and `useStandalone(): string | null`.

- [ ] **Step 1: Write the failing unit test**

Create `web/lib/standalone.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { resolveStandalone } from "./standalone";

describe("resolveStandalone", () => {
  it("reads the flag off the opening URL", () => {
    expect(resolveStandalone("?window=standalone", null)).toBe("standalone");
  });

  it("keeps the flag after the query string is gone", () => {
    // Client-side navigation inside the window drops the query, so the value
    // has to come from sessionStorage on every subsequent render.
    expect(resolveStandalone("", "standalone")).toBe("standalone");
  });

  it("is null in an ordinary window", () => {
    expect(resolveStandalone("", null)).toBeNull();
  });

  it("ignores a value it does not recognise", () => {
    // The flag reaches this from a URL, which a user can type.
    expect(resolveStandalone("?window=embedded", null)).toBeNull();
    expect(resolveStandalone("", "embedded")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd web && npm run test:unit`
Expected: FAIL — cannot resolve `./standalone`.

- [ ] **Step 3: Implement**

Create `web/lib/standalone.ts`:

```ts
"use client";

import { useEffect, useState } from "react";

/**
 * Is this window a popped-out mode, and which one?
 *
 * The flag arrives once, on the opening URL, and is kept in **sessionStorage**
 * rather than localStorage: sessionStorage is scoped to a single window, so it
 * survives client-side navigation and reload inside the popped-out window and
 * cannot leak into the main one. localStorage would make every window think it
 * was standalone.
 */

const KEY = "argus-standalone";
const KNOWN = new Set(["standalone"]);

/** Pure so it can be tested without a window. `search` is `location.search`. */
export function resolveStandalone(search: string, stored: string | null): string | null {
  const fromUrl = new URLSearchParams(search).get("window");
  const value = fromUrl ?? stored;
  return value !== null && KNOWN.has(value) ? value : null;
}

export function useStandalone(): string | null {
  // Starts null so server and first client render agree; a popped-out window
  // paints its full chrome for one frame, which is correct — a hydration
  // mismatch here would be worse than a frame of the ordinary top bar.
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.sessionStorage.getItem(KEY);
    } catch {
      // sessionStorage throws in some embedded contexts; the window then
      // simply renders as an ordinary one.
    }
    const resolved = resolveStandalone(window.location.search, stored);
    if (resolved !== null) {
      try {
        window.sessionStorage.setItem(KEY, resolved);
      } catch {
        // Best-effort: without it the flag is lost on the next navigation,
        // which degrades to ordinary chrome rather than breaking.
      }
    }
    setValue(resolved);
  }, []);

  return value;
}
```

- [ ] **Step 4: Run it and watch it pass**

Run: `cd web && npm run test:unit`
Expected: PASS.

- [ ] **Step 5: Add the pop-out control**

Create `web/components/notebook/PopOutButton.tsx`:

```tsx
"use client";

import { useToast } from "@/components/Toast";
import { useStandalone } from "@/lib/standalone";

/** Named target, so a second click focuses the window rather than opening another. */
export const NOTEBOOK_WINDOW = "argus-notebook";

/**
 * Move the Notebook into its own window.
 *
 * One `window.open` serves three environments. In a browser it is a real
 * second window. In Electron it is intercepted by `setWindowOpenHandler`
 * (desktop/main.js), which allows same-origin `/notebook` URLs and denies
 * everything else exactly as before. Playwright addresses the result through
 * `page.waitForEvent("popup")`.
 */
export default function PopOutButton() {
  const { show } = useToast();
  const standalone = useStandalone();

  // The window that *is* the pop-out must not offer to pop itself out.
  if (standalone !== null) return null;

  return (
    <button
      type="button"
      onClick={() => {
        const opened = window.open(
          "/notebook?window=standalone",
          NOTEBOOK_WINDOW,
          "width=1280,height=880",
        );
        if (opened === null) {
          show("your browser blocked the window — allow pop-ups for Argus", { tone: "error" });
          return;
        }
        opened.focus();
      }}
      className="border border-line px-2 py-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
    >
      pop out ↗
    </button>
  );
}
```

Render it in `NotebookStatusLine.tsx`'s right-hand slot.

- [ ] **Step 6: Give `TopBar` its compact variant**

In `web/components/TopBar.tsx`, call `useStandalone()`. When it is non-null, render a reduced bar: the `ARGUS · NOTEBOOK` wordmark, `<NotebookTabs />`, `<JobTray />` and the engine-picker trigger — and **not** the six-mode strip, because this window cannot leave the mode it exists to hold. Keep the engine-picker trigger's `aria-label={`Model: ${name}`}`; the e2e suite addresses it that way.

- [ ] **Step 7: Write and run the e2e**

Append to `web/e2e/notebook.spec.ts`:

```ts
test("the notebook opens in a window of its own", async ({ page, context }) => {
  await page.goto("/notebook");
  const [popup] = await Promise.all([
    context.waitForEvent("page"),
    page.getByRole("button", { name: /pop out/i }).click(),
  ]);
  await popup.waitForLoadState();
  await expect(popup).toHaveURL(/\/notebook\?window=standalone$/);

  // Its own chrome: the notebook sub-nav is there, the six-mode strip is not.
  await expect(popup.getByRole("tab", { name: "FLASHCARDS" })).toBeVisible();
  await expect(popup.getByRole("tab", { name: "RESEARCH" })).toHaveCount(0);

  // And the flag outlives the query string.
  await popup.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(popup.getByRole("tab", { name: "RESEARCH" })).toHaveCount(0);
});
```

Run: `cd web && npx playwright test notebook.spec.ts`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/lib/standalone.ts web/lib/standalone.test.ts \
        web/components/notebook/PopOutButton.tsx web/components/TopBar.tsx \
        web/components/notebook/NotebookStatusLine.tsx web/e2e/notebook.spec.ts
git commit -m "feat(notebook): the notebook can move into a window of its own"
```

---

## Task 6: The Electron shell allows exactly one extra window

**Files:**
- Create: `desktop/lib/windows.js`, `desktop/tests/windows.test.js`
- Modify: `desktop/main.js:255-290`
- Modify: `desktop/package.json` (a `test` script)

**Interfaces:**
- Consumes: `NOTEBOOK_WINDOW` name from Task 5 (informational — Electron does not use it).
- Produces: `isNotebookUrl(url: string, origin: string): boolean`.

- [ ] **Step 1: Write the failing test**

`desktop/` is plain CommonJS with no test runner; Node 20's built-in `node:test` needs no dependency. Create `desktop/tests/windows.test.js`:

```js
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { isNotebookUrl } = require("../lib/windows");

const ORIGIN = "http://127.0.0.1:41234";

test("allows the notebook route on the app's own origin", () => {
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook`, ORIGIN), true);
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook?window=standalone`, ORIGIN), true);
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook/flashcards/3/review`, ORIGIN), true);
});

test("refuses any other route, so the deny-by-default policy survives", () => {
  assert.equal(isNotebookUrl(`${ORIGIN}/dashboard`, ORIGIN), false);
  assert.equal(isNotebookUrl(`${ORIGIN}/`, ORIGIN), false);
});

test("refuses another origin wearing the notebook path", () => {
  assert.equal(isNotebookUrl("https://evil.example/notebook", ORIGIN), false);
  assert.equal(isNotebookUrl("http://127.0.0.1:9999/notebook", ORIGIN), false);
});

test("refuses a path that merely starts with the letters", () => {
  // /notebookery is not the notebook.
  assert.equal(isNotebookUrl(`${ORIGIN}/notebookery`, ORIGIN), false);
});

test("refuses junk rather than throwing", () => {
  assert.equal(isNotebookUrl("not a url", ORIGIN), false);
  assert.equal(isNotebookUrl("", ORIGIN), false);
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `node --test desktop/tests/windows.test.js`
Expected: FAIL — cannot find `../lib/windows`.

- [ ] **Step 3: Implement the guard**

Create `desktop/lib/windows.js`:

```js
"use strict";

/**
 * The one exception to "deny every popup".
 *
 * `main.js` denied all `window.open` calls by policy. The Notebook needs a
 * real second window, so this narrows the exception to a single origin and a
 * single route rather than relaxing the handler. Parsed with `URL` rather than
 * matched with a prefix, so `/notebookery`, a userinfo trick
 * (`http://127.0.0.1:41234@evil.example/notebook`) and a different port are all
 * refused.
 */
function isNotebookUrl(url, origin) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.origin !== origin) return false;
  return parsed.pathname === "/notebook" || parsed.pathname.startsWith("/notebook/");
}

module.exports = { isNotebookUrl };
```

- [ ] **Step 4: Run it and watch it pass**

Run: `node --test desktop/tests/windows.test.js`
Expected: 5 tests pass.

Add to `desktop/package.json` scripts: `"test": "node --test tests/"`.

- [ ] **Step 5: Wire it into the shell**

In `desktop/main.js`, add `let notebookWindow = null;` beside the other window handles, require `isNotebookUrl`, and replace the window-open handler in `createMainWindow`:

```js
  // Exactly one extra window: the Notebook. Everything else keeps the old
  // policy -- real links go to the OS browser, nothing else opens.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isNotebookUrl(url, origin)) {
      if (notebookWindow && !notebookWindow.isDestroyed()) {
        notebookWindow.focus();
        return { action: "deny" };
      }
      return {
        action: "allow",
        // webPreferences are re-declared rather than assumed inherited.
        // `additionalArguments` carries the backend port that preload.js reads
        // into window.__ARGUS__; without it every API call in the popped
        // window resolves against the Next origin and 404s -- and only when
        // packaged, because dev is same-origin through the rewrite.
        overrideBrowserWindowOptions: {
          width: 1280,
          height: 880,
          minWidth: 900,
          minHeight: 600,
          backgroundColor: "#06040c",
          title: "Argus · Notebook",
          icon: path.join(__dirname, "build", "icon.ico"),
          ...conf.notebookBounds(),
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            webSecurity: true,
            preload: path.join(__dirname, "preload.js"),
            additionalArguments: [`--argus-api=${apiOrigin}`],
          },
        },
      };
    }
    if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  // The child gets the same navigation guard and the same deny-by-default
  // popup policy, so it cannot spawn grandchildren unchecked.
  mainWindow.webContents.on("did-create-window", (child) => {
    notebookWindow = child;
    child.setMenuBarVisibility(false);
    child.webContents.setWindowOpenHandler(({ url }) => {
      if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
      return { action: "deny" };
    });
    child.webContents.on("will-navigate", (event, url) => {
      if (url.startsWith(origin)) return;
      event.preventDefault();
      if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
    });
    const remember = () => {
      if (!child.isDestroyed()) conf.setNotebookBounds(child.getBounds());
    };
    child.on("resize", remember);
    child.on("move", remember);
    child.on("closed", () => {
      notebookWindow = null;
    });
  });
```

Closing the main window must also close the Notebook, or the app has no visible window and no way back. In `mainWindow.on("closed", …)`:

```js
  mainWindow.on("closed", () => {
    mainWindow = null;
    if (notebookWindow && !notebookWindow.isDestroyed()) notebookWindow.close();
  });
```

- [ ] **Step 6: Add the bounds accessors**

In `desktop/lib/config.js`, mirroring whatever read/write helper is already there, add `notebookBounds()` returning `{}` when unset (so the `...spread` above is a no-op on first open) and `setNotebookBounds(bounds)` persisting `{x, y, width, height}`.

- [ ] **Step 7: Commit**

```bash
git add desktop/lib/windows.js desktop/tests/windows.test.js desktop/main.js \
        desktop/lib/config.js desktop/package.json
git commit -m "feat(desktop): the notebook gets a real window, and nothing else does"
```

---

## Task 7: Cross-window awareness

Lowest-value item in the plan and the safest to cut. It exists so the mode *moves* rather than being shown twice.

**Files:**
- Create: `web/lib/windowBus.ts`
- Modify: `web/components/TopBar.tsx`, `web/components/notebook/PopOutButton.tsx`

**Interfaces:**
- Consumes: `NOTEBOOK_WINDOW` from Task 5.
- Produces: `useNotebookWindowOpen(): boolean`, `announceStandalone(): () => void`.

- [ ] **Step 1: Implement the bus**

Create `web/lib/windowBus.ts`:

```ts
"use client";

import { useEffect, useState } from "react";

/**
 * Which Argus windows are open, so the mode moves instead of duplicating.
 *
 * `BroadcastChannel` is a standard API available both in the browser and in
 * the Electron renderer, and it is same-origin by definition — which is the
 * whole security story here. Every consumer degrades to "no other window" if
 * it is unavailable, so nothing depends on it existing.
 */

const CHANNEL = "argus-windows";

function channel(): BroadcastChannel | null {
  try {
    return new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

/** Called by the standalone window: announce presence, and absence on close. */
export function announceStandalone(): () => void {
  const bus = channel();
  if (bus === null) return () => {};
  const say = (open: boolean) => bus.postMessage({ notebook: open });
  say(true);
  // Answer late joiners: a main window opened after the popup would otherwise
  // never learn it exists.
  bus.onmessage = (event) => {
    if (event.data?.ask === "notebook") say(true);
  };
  const bye = () => say(false);
  window.addEventListener("beforeunload", bye);
  return () => {
    bye();
    window.removeEventListener("beforeunload", bye);
    bus.close();
  };
}

/** Called by the main window. */
export function useNotebookWindowOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const bus = channel();
    if (bus === null) return;
    bus.onmessage = (event) => {
      if (typeof event.data?.notebook === "boolean") setOpen(event.data.notebook);
    };
    bus.postMessage({ ask: "notebook" });
    return () => bus.close();
  }, []);
  return open;
}
```

- [ ] **Step 2: Use it on both sides**

`TopBar` calls `useNotebookWindowOpen()`. While true, the NOTEBOOK tab renders `NOTEBOOK ↗` and its click calls `window.open("", NOTEBOOK_WINDOW)?.focus()` instead of `setMode("notebook")`. `PopOutButton`, after a successful open, calls `router.push("/dashboard")` so the main window yields the mode. The standalone `TopBar` variant calls `announceStandalone()` in an effect and returns its cleanup.

- [ ] **Step 3: Verify nothing regressed**

Run: `cd web && npx tsc --noEmit && npm run lint && npx playwright test notebook.spec.ts`
Expected: clean and passing. (The popup e2e still holds: `waitForEvent("page")` fires before the main window navigates away.)

- [ ] **Step 4: Commit**

```bash
git add web/lib/windowBus.ts web/components/TopBar.tsx web/components/notebook/PopOutButton.tsx
git commit -m "feat(notebook): popping out moves the mode instead of cloning it"
```

---

# Phase 3 — Flashcards

## Task 8: Cards become rows, and every review survives

The migration's whole trick: `flashcard_reviews.card_id` holds `"{deck_id}:{index}"` (generated at `store.py:161`), so if migrated cards carry that same string as `card_ref`, **`flashcard_reviews` needs no change at all**.

**Files:**
- Modify: `backend/core/db.py` (SCHEMA + `init_schema`)
- Create: `tests/features/flashcards/test_migration.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `flashcard_cards`, `flashcard_match_scores`; `flashcard_decks` columns `description`, `source`, `updated_at`; `_migrate_flashcard_cards(conn)`.

- [ ] **Step 1: Write the failing test**

Create `tests/features/flashcards/test_migration.py`:

```python
"""The cards_json -> rows migration must not cost a single review."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.core.db import connect, init_schema


def _legacy_deck(conn: sqlite3.Connection) -> int:
    """A deck exactly as the pre-rows code wrote it, plus review history."""
    cursor = conn.execute(
        "INSERT INTO flashcard_decks (course, title, cards_json) VALUES (?, ?, ?)",
        ("CS201", "CS201 flashcards", "[]"),
    )
    deck_id = int(cursor.lastrowid)
    cards = [
        {"id": f"{deck_id}:0", "front": "big-O of merge sort", "back": "$O(n \\log n)$"},
        {"id": f"{deck_id}:1", "front": "stable sort?", "back": "yes"},
    ]
    conn.execute(
        "UPDATE flashcard_decks SET cards_json = ? WHERE id = ?", (json.dumps(cards), deck_id)
    )
    conn.execute(
        "INSERT INTO flashcard_reviews"
        " (card_id, deck_id, grade, state, step, stability, difficulty, due_at, last_review_at)"
        " VALUES (?, ?, 'good', 2, 0, 12.5, 4.75, '2099-01-01T00:00:00+00:00', NULL)",
        (f"{deck_id}:0", deck_id),
    )
    conn.commit()
    return deck_id


def test_migration_creates_a_row_per_card_keyed_to_its_reviews(tmp_path: Path) -> None:
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    deck_id = _legacy_deck(conn)

    init_schema(conn)  # the migration runs here

    rows = conn.execute(
        "SELECT card_ref, front, back, position FROM flashcard_cards"
        " WHERE deck_id = ? ORDER BY position",
        (deck_id,),
    ).fetchall()
    assert [row["card_ref"] for row in rows] == [f"{deck_id}:0", f"{deck_id}:1"]
    assert rows[0]["front"] == "big-O of merge sort"
    assert rows[0]["back"] == "$O(n \\log n)$"
    assert [row["position"] for row in rows] == [0, 1]


def test_migration_leaves_review_history_attached(tmp_path: Path) -> None:
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    deck_id = _legacy_deck(conn)

    init_schema(conn)

    review = conn.execute(
        "SELECT r.stability, r.difficulty FROM flashcard_reviews r"
        " JOIN flashcard_cards c ON c.card_ref = r.card_id AND c.deck_id = r.deck_id"
        " WHERE c.position = 0 AND c.deck_id = ?",
        (deck_id,),
    ).fetchone()
    assert review is not None, "the review no longer joins to its card"
    assert review["stability"] == 12.5


def test_migration_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    deck_id = _legacy_deck(conn)

    init_schema(conn)
    init_schema(conn)
    init_schema(conn)

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
    ).fetchone()["n"]
    assert count == 2, "a re-run duplicated the deck's cards"


def test_new_deck_columns_default_sensibly(tmp_path: Path) -> None:
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    deck_id = _legacy_deck(conn)
    init_schema(conn)

    row = conn.execute(
        "SELECT source, description FROM flashcard_decks WHERE id = ?", (deck_id,)
    ).fetchone()
    # Everything that existed before authoring did was generated.
    assert row["source"] == "generated"
    assert row["description"] == ""
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/test_migration.py -q`
Expected: FAIL — `no such table: flashcard_cards`.

- [ ] **Step 3: Add the tables to `SCHEMA`**

In `backend/core/db.py`, after the `flashcard_reviews` index:

```sql
-- Cards are rows, not a JSON blob, because they are now authored: created by
-- hand, edited, reordered, starred and imported one at a time.
--
-- `card_ref` is the key `flashcard_reviews.card_id` joins on. Migrated cards
-- keep the "{deck_id}:{index}" string the blob-era code generated, which is
-- what lets the migration leave every review row untouched. New cards get
-- "c<uuid4 hex>", which cannot collide with that shape.
CREATE TABLE IF NOT EXISTS flashcard_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES flashcard_decks(id),
    card_ref    TEXT    NOT NULL,
    front       TEXT    NOT NULL,
    back        TEXT    NOT NULL,
    hint        TEXT,
    position    INTEGER NOT NULL,
    starred     INTEGER NOT NULL DEFAULT 0,
    suspended   INTEGER NOT NULL DEFAULT 0,
    source_path TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_flashcard_cards_ref
    ON flashcard_cards(deck_id, card_ref);
CREATE INDEX IF NOT EXISTS idx_flashcard_cards_deck
    ON flashcard_cards(deck_id, position);

CREATE TABLE IF NOT EXISTS flashcard_match_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER NOT NULL REFERENCES flashcard_decks(id),
    elapsed_ms INTEGER NOT NULL,
    pairs      INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

Also add a comment above `flashcard_decks.cards_json` recording that it is no longer read: dropping a column from a table a foreign key references means a create/copy/drop/rename rebuild, which is real risk for a cosmetic gain. New decks write `'[]'`.

- [ ] **Step 4: Write the migration**

In `init_schema`, after the ingest-column block:

```python
    deck_columns = {row["name"] for row in conn.execute("PRAGMA table_info(flashcard_decks)")}
    if "description" not in deck_columns:
        conn.execute("ALTER TABLE flashcard_decks ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "source" not in deck_columns:
        # Everything that existed before decks could be authored was generated,
        # which is exactly what the default backfills.
        conn.execute(
            "ALTER TABLE flashcard_decks ADD COLUMN source TEXT NOT NULL DEFAULT 'generated'"
        )
    if "updated_at" not in deck_columns:
        # No DEFAULT (datetime('now')): SQLite rejects a non-constant default
        # in ALTER TABLE ADD COLUMN. Backfilled from created_at below.
        conn.execute("ALTER TABLE flashcard_decks ADD COLUMN updated_at TEXT")
        conn.execute("UPDATE flashcard_decks SET updated_at = created_at WHERE updated_at IS NULL")
    _migrate_flashcard_cards(conn)
```

And the function itself, beside `_migrate_scan_key`:

```python
def _migrate_flashcard_cards(conn: sqlite3.Connection) -> None:
    """Explode every legacy ``cards_json`` blob into ``flashcard_cards`` rows.

    Additive by construction: each row's ``card_ref`` is the very
    ``"{deck_id}:{index}"`` string the blob-era generator wrote into
    ``flashcard_decks.cards_json`` and that ``flashcard_reviews.card_id``
    already holds, so **no review row is read, rewritten, or backfilled** and
    every card keeps its FSRS state.

    Idempotent: a deck that already has rows is skipped, so this can run on
    every ``init_schema`` call for the life of the database.
    """
    migrated = {
        row["deck_id"] for row in conn.execute("SELECT DISTINCT deck_id FROM flashcard_cards")
    }
    for row in conn.execute("SELECT id, cards_json FROM flashcard_decks").fetchall():
        deck_id = row["id"]
        if deck_id in migrated:
            continue
        try:
            cards = json.loads(row["cards_json"])
        except (TypeError, ValueError):
            # A hand-edited or truncated blob is not worth failing every
            # database open over; the deck simply arrives empty and can be
            # re-imported.
            logger.warning("flashcard deck %s has unreadable cards_json; migrating it empty", deck_id)
            continue
        if not cards:
            continue
        conn.executemany(
            "INSERT INTO flashcard_cards (deck_id, card_ref, front, back, position)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (deck_id, card.get("id") or f"{deck_id}:{index}", card["front"], card["back"], index)
                for index, card in enumerate(cards)
                if card.get("front") and card.get("back")
            ],
        )
    conn.commit()
```

`db.py` needs `import json` and a module logger if it lacks them.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/ -q`
Expected: PASS, including the pre-existing `test_flashcards.py`.

- [ ] **Step 6: Commit**

```bash
git add backend/core/db.py tests/features/flashcards/test_migration.py
git commit -m "feat(flashcards): cards become rows, and every review survives the move"
```

---

## Task 9: Parsing, scheduling, and a store over rows

**Files:**
- Create: `backend/features/flashcards/parsing.py`, `backend/features/flashcards/scheduler.py`
- Modify: `backend/features/flashcards/store.py` (rewritten over rows)
- Create: `tests/features/flashcards/test_parsing.py`, `tests/features/flashcards/test_cards.py`
- Modify: `tests/features/flashcards/test_flashcards.py`

**Interfaces:**
- Consumes: the Task 8 schema.
- Produces:
  - `parsing.parse_qa_pairs(text: str) -> list[tuple[str, str]]` (moved verbatim from `store.py:99`)
  - `parsing.parse_delimited(text: str, *, field: str, row: str) -> list[tuple[str, str]]`
  - `scheduler.grade(card_state: dict | None, grade: str, now: datetime) -> GradeResult`
  - `scheduler.preview(card_state: dict | None, now: datetime) -> dict[str, str]`
  - `store.create_deck / list_decks / load_deck / update_deck / delete_deck`
  - `store.add_cards / update_card / delete_card / reorder_cards / set_starred`
  - `store.due_cards(conn, deck_id, now=None) -> list[DueCard]` (now carrying `preview` and `hint`)

- [ ] **Step 1: Write the failing parsing tests**

Create `tests/features/flashcards/test_parsing.py`:

```python
from __future__ import annotations

from backend.features.flashcards.parsing import parse_delimited, parse_qa_pairs


def test_qa_pairs_span_multiple_lines() -> None:
    text = "Q:: what is a monad\nreally\nA:: a monoid in the category\nof endofunctors\n"
    assert parse_qa_pairs(text) == [
        ("what is a monad\nreally", "a monoid in the category\nof endofunctors")
    ]


def test_qa_pair_missing_its_answer_is_dropped() -> None:
    assert parse_qa_pairs("Q:: lonely question\nQ:: paired\nA:: yes") == [("paired", "yes")]


def test_delimited_tab_and_newline() -> None:
    assert parse_delimited("front\tback\nsecond\tpair", field="tab", row="newline") == [
        ("front", "back"),
        ("second", "pair"),
    ]


def test_delimited_comma_and_semicolon() -> None:
    assert parse_delimited("a,1; b,2", field="comma", row="semicolon") == [("a", "1"), ("b", "2")]


def test_delimited_splits_on_the_first_field_delimiter_only() -> None:
    # A definition containing a comma must survive comma-delimited import;
    # splitting greedily would silently truncate every such card.
    assert parse_delimited("term,a, b, and c", field="comma", row="newline") == [
        ("term", "a, b, and c")
    ]


def test_delimited_skips_rows_with_no_delimiter_or_an_empty_half() -> None:
    assert parse_delimited("good\tpair\nlonely\n\tnofront\nnoback\t", field="tab", row="newline") == [
        ("good", "pair")
    ]


def test_delimited_tolerates_crlf() -> None:
    assert parse_delimited("a\tb\r\nc\td", field="tab", row="newline") == [("a", "b"), ("c", "d")]
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/test_parsing.py -q`
Expected: FAIL — no module `parsing`.

- [ ] **Step 3: Write `parsing.py`**

Move `parse_qa_pairs` out of `store.py` verbatim (it is correct and tested), and add:

```python
FIELD_DELIMITERS = {"tab": "\t", "comma": ",", "dash": "-"}
ROW_DELIMITERS = {"newline": "\n", "semicolon": ";"}


def parse_delimited(text: str, *, field: str, row: str) -> list[tuple[str, str]]:
    """Parse pasted rows into (front, back) pairs.

    Mirrors the import affordance every flashcard tool offers: choose what
    separates the two halves and what separates the rows, paste, preview.

    Split on the **first** field delimiter only — a definition legitimately
    contains commas and dashes, and splitting greedily would truncate exactly
    the cards worth writing. Rows lacking a delimiter, or with either half
    empty, are dropped rather than imported half-formed.
    """
    field_sep = FIELD_DELIMITERS[field]
    row_sep = ROW_DELIMITERS[row]
    pairs: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").split(row_sep):
        head, sep, tail = line.partition(field_sep)
        if not sep:
            continue
        front, back = head.strip(), tail.strip()
        if front and back:
            pairs.append((front, back))
    return pairs
```

- [ ] **Step 4: Run and watch it pass**

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/test_parsing.py -q`
Expected: 7 pass.

- [ ] **Step 5: Write the failing scheduler test**

Create `tests/features/flashcards/test_scheduler.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from backend.features.flashcards.scheduler import grade, preview

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def test_preview_offers_all_four_grades_in_increasing_order() -> None:
    intervals = preview(None, NOW)
    assert set(intervals) == {"again", "hard", "good", "easy"}


def test_preview_matches_what_committing_actually_does() -> None:
    # The preview is the promise on the button. If it can drift from the
    # commit, the button lies.
    for name in ("again", "hard", "good", "easy"):
        promised = preview(None, NOW)[name]
        committed = grade(None, name, NOW)
        assert promised == committed.due_label, f"{name} previewed {promised}, committed {committed.due_label}"


def test_grade_rejects_an_unknown_name() -> None:
    import pytest

    from backend.features.flashcards.scheduler import SchedulerError

    with pytest.raises(SchedulerError):
        grade(None, "brilliant", NOW)
```

- [ ] **Step 6: Write `scheduler.py`**

Move the `fsrs` interaction out of `store.py` — `GRADE_TO_RATING`, the `FsrsCard` reconstruction, `Scheduler().review_card`. Add:

```python
def preview(state: dict[str, Any] | None, now: datetime) -> dict[str, str]:
    """What each of the four grades would do, without doing any of them.

    ``fsrs.Scheduler`` is pure: reviewing a card returns a new one and mutates
    nothing, so all four futures can be computed from one state. This is what
    turns the grade bar from four unlabelled verbs into a real choice.
    """
    return {name: _humanise(_review(state, name, now).due, now) for name in GRADE_TO_RATING}


def _humanise(due: datetime, now: datetime) -> str:
    """`10m`, `4d`, `3mo` — the unit a learner actually reasons in."""
    seconds = max(0, int((due - now).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"
```

`GradeResult` gains `due_label: str` from the same `_humanise`, so the preview and the commit cannot drift — they call one function.

- [ ] **Step 7: Rewrite `store.py` over rows**

`load_deck` reads `flashcard_cards` ordered by `position`. `due_cards` keeps its existing latest-review-wins logic (`_latest_reviews` is unchanged — it already keys on the string `card_id`) and each `DueCard` gains `hint: str | None` and `preview: dict[str, str]`. Add the CRUD listed in **Interfaces**; `add_cards` assigns `card_ref = "c" + uuid4().hex` and appends positions after the current maximum; `reorder_cards` takes an ordered list of `card_ref` and rewrites `position`; `delete_card` also deletes that card's `flashcard_reviews` rows (children first, as `delete_deck` already does — `PRAGMA foreign_keys=ON` with no `ON DELETE CASCADE`).

`generate_deck` — the `flashcards.md` parser — moves to `vault.py` in Task 11. Until then keep it importable so `router.py` stays green.

- [ ] **Step 8: Write and run the store tests**

Create `tests/features/flashcards/test_cards.py` covering: create a courseless deck; add cards and read them back in order; update a card's front/back/hint bumps `updated_at`; reorder rewrites positions; delete removes its reviews; `due_cards` returns new cards immediately with a four-key `preview`; a suspended card never appears in the due queue; starring persists.

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/ -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend/features/flashcards/ tests/features/flashcards/
git commit -m "feat(flashcards): a store over rows, with parsing and scheduling split out"
```

---

## Task 10: The card and deck endpoints

**Files:**
- Modify: `backend/features/flashcards/router.py`
- Modify: `web/lib/api.ts`
- Modify: `tests/features/flashcards/test_flashcards.py`

**Interfaces:**
- Consumes: Task 9's store functions.
- Produces:

```
POST   /api/flashcards/decks                      {title, course?, description?} -> DeckSummary
PATCH  /api/flashcards/decks/{id}                 {title?, description?, course?}
GET    /api/flashcards/decks/{id}                 -> DeckDetail (deck + all cards)
POST   /api/flashcards/decks/{id}/cards           {cards: [{front, back, hint?}]} -> {added}
PATCH  /api/flashcards/decks/{id}/cards/{ref}     {front?, back?, hint?, starred?, suspended?}
DELETE /api/flashcards/decks/{id}/cards/{ref}     -> {reviews_removed}
POST   /api/flashcards/decks/{id}/cards/reorder   {order: [ref, ...]}
POST   /api/flashcards/decks/{id}/match-score     {elapsed_ms, pairs} -> {best_ms}
GET    /api/flashcards/decks/{id}/match-best      -> {best_ms: int | null}
```

- [ ] **Step 1: Note the breaking change**

`POST /decks` currently takes `{course}` and generates from `flashcards.md`. It now creates an empty deck. Generation moves to `/decks/generate` (Task 12) and `flashcards.md` becomes one importable note among many (Task 11). `generateFlashcardDeck()` at `web/lib/api.ts:898` and both its callsites move with it, and `test_api_generate_deck_missing_flashcards_md_is_422` is rewritten against the import route that now owns that error.

- [ ] **Step 2: Write the failing router tests**

Extend `tests/features/flashcards/test_flashcards.py` with a case per endpoint, asserting status codes for the failure paths too: creating a card in a deck that does not exist is 404; reordering with a `card_ref` not in the deck is 422; `PATCH` with an empty body is a no-op 200; a deck title of only whitespace is 422.

- [ ] **Step 3: Run and watch them fail, then implement the routes**

Each route follows the file's existing shape: `conn = db()` / `try` / `except FlashcardsError -> HTTPException` / `finally: conn.close()`.

- [ ] **Step 4: Add the client functions**

In `web/lib/api.ts`, add typed wrappers for each endpoint plus `useDeck(deckId)` (SWR on `/api/flashcards/decks/{id}`). Keep every call going through `mutateJSON`/`fetcher`.

- [ ] **Step 5: Verify**

Run: `.venv/Scripts/python -m pytest tests/features/flashcards/ -q && ruff check . && cd web && npx tsc --noEmit`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add backend/features/flashcards/router.py web/lib/api.ts tests/features/flashcards/
git commit -m "feat(flashcards): decks and cards are created, edited and reordered for real"
```

---

## Task 11: Import from any note, export to markdown

This is the fix for the core absurdity: ingest has been writing `Q::`/`A::` tails into every generated note since the note-quality work, and the deck generator could only read one file that nothing writes.

**Files:**
- Create: `backend/features/flashcards/vault.py`
- Create: `tests/features/flashcards/test_vault.py`
- Modify: `backend/features/flashcards/router.py`

**Interfaces:**
- Consumes: `parsing.parse_qa_pairs`; `store.add_cards`; `backend/vault/writer.py`.
- Produces:
  - `vault.import_from_note(vault_path, conn, deck_id, rel_path) -> int`
  - `vault.export_deck(vault_path, conn, deck_id, *, taxonomy) -> str`
  - `POST /api/flashcards/decks/{id}/import/note {path}` -> `{added}`
  - `POST /api/flashcards/decks/{id}/export` -> `{path}`

- [ ] **Step 1: Write the failing tests**

Cover: importing from a course's `flashcards.md`; importing from an arbitrary `50-Reference/whatever.md` carrying a `Q::` tail; a note with no pairs is 422 with a message naming the note; a path escaping the vault is refused; export writes a file `parse_qa_pairs` reads back to the same pairs; export of a courseless deck is 422 (there is no folder to write into) and says so.

- [ ] **Step 2: Implement**

`import_from_note` resolves the path against the vault, refuses anything outside it, parses, and calls `store.add_cards` recording `source_path`. `export_deck` renders `Q:: …\nA:: …` blocks and writes through the writer so invariant I1's git snapshot applies.

- [ ] **Step 3: Verify and commit**

```bash
.venv/Scripts/python -m pytest tests/features/flashcards/ -q
git add backend/features/flashcards/vault.py backend/features/flashcards/router.py tests/features/flashcards/test_vault.py
git commit -m "feat(flashcards): cards import from any note, and export back to markdown"
```

---

## Task 12: Deck generation as a background job

**Files:**
- Create: `backend/features/flashcards/generate.py`, `backend/features/flashcards/jobs.py`
- Create: `tests/features/flashcards/test_generate.py`
- Modify: `backend/features/flashcards/router.py`, `backend/main.py:254`

**Interfaces:**
- Consumes: `backend/features/study/corpus.py::course_corpus`; `store.add_cards`; `backend/features/ingest/store` job functions; the `Generator` type from `study/practice_exam.py`.
- Produces:
  - `generate.generate_cards(generator, corpus, course, n) -> list[GeneratedCard]`
  - `jobs.run_deck_job(job_id, *, settings, generator, corpus, course, deck_id, n) -> None`
  - `POST /api/flashcards/decks/generate {course, sources, model, n}` -> `202 {job_id}`

- [ ] **Step 1: Write the failing tests**

Mirror `tests/features/study/`'s generator fakes. Cover: a well-formed model response becomes cards carrying their citation; a response with no parseable pairs raises `FlashcardsError`; the job records `deck_id` and the card count into `params` on success and records the message against its own row on failure **without raising**, exactly as `study/jobs.py::_fail` does (the body runs on a daemon thread, so an escaping exception surfaces only in a log nobody watches).

- [ ] **Step 2: Implement**

`generate.py` prompts for `Q::`/`A::` pairs so `parse_qa_pairs` is the single parser, and passes the shared output contract used by every other prose prompt (`backend/agent/prompts/`) so LaTeX is typeset consistently. `jobs.py` mirrors `study/jobs.py` exactly — same `_job_item`, same `GENERATING = "summarizing"` stage reuse (the value lives in a `CHECK` constraint; SQLite cannot alter one, and a new value would cost a table rebuild to buy a label the frontend can say for itself), same never-raises contract.

`build_flashcards_router` now needs `generator` and `job_runner`, so `backend/main.py:254` passes them the way `build_study_router` is called at `:237`.

- [ ] **Step 3: Verify and commit**

```bash
.venv/Scripts/python -m pytest tests/features/flashcards/ -q && ruff check .
git add backend/features/flashcards/ backend/main.py tests/features/flashcards/
git commit -m "feat(flashcards): decks generate from the selected sources, in the background"
```

---

## Task 13: The deck library and the editor

**Files:**
- Create: `web/lib/flashcards/parsing.ts`, `web/lib/flashcards/parsing.test.ts`
- Create: `web/components/notebook/flashcards/DeckList.tsx`, `DeckEditor.tsx`, `ImportDialog.tsx`
- Create: `web/app/(dashboard)/notebook/flashcards/[deckId]/page.tsx`
- Modify: `web/app/(dashboard)/notebook/flashcards/page.tsx`
- Delete: `web/components/notebook/Flashcards.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: Task 10's client functions.
- Produces: `parseDelimited(text, field, row): { front: string; back: string }[]` — the browser twin of `parsing.parse_delimited`, so the import preview is honest before anything is sent.

- [ ] **Step 1: Write the failing unit test**

`web/lib/flashcards/parsing.test.ts` mirrors `test_parsing.py`'s delimited cases exactly, including first-delimiter-only splitting and CRLF. The two implementations must agree; the preview lies otherwise.

- [ ] **Step 2: Implement `parseDelimited`, run, watch it pass**

- [ ] **Step 3: Build the three components**

`DeckList` — the library: rows of deck title, card count, due count, source badge; `+ NEW DECK` inline form; delete via `useConfirm`. `DeckEditor` — the card table, which *is* the browse surface: a row per card with `front`, `back`, `hint` inputs, `Tab` between fields, `Enter` on the last field appending a row, drag-free reorder via `▲`/`▼` buttons, `×` per row. `ImportDialog` — a `Dialog` with two tabs, paste (textarea + two `SegmentedControl`s + live preview count) and from-note (path input).

- [ ] **Step 4: Write the e2e**

```ts
test("a deck is created, filled by hand, and filled by paste", async ({ page }) => {
  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: "+ NEW DECK" }).click();
  await page.getByLabel("Deck title").fill("Manual deck");
  await page.getByRole("button", { name: "CREATE" }).click();

  await page.getByRole("link", { name: /Manual deck/ }).click();
  await page.getByLabel("Front").last().fill("capital of France");
  await page.getByLabel("Back").last().fill("Paris");
  await page.getByRole("button", { name: "+ ADD CARD" }).click();
  await expect(page.getByText("1 card")).toBeVisible();

  await page.getByRole("button", { name: "IMPORT" }).click();
  await page.getByLabel("Paste rows").fill("a\t1\nb\t2");
  await expect(page.getByText("2 cards will be added")).toBeVisible();
  await page.getByRole("button", { name: "IMPORT 2" }).click();
  await expect(page.getByText("3 cards")).toBeVisible();
});
```

- [ ] **Step 5: Verify and commit**

```bash
cd web && npm run test:unit && npx tsc --noEmit && npm run lint && npx playwright test notebook.spec.ts
git add web/lib/flashcards web/components/notebook/flashcards "web/app/(dashboard)/notebook/flashcards" web/e2e/notebook.spec.ts
git rm web/components/notebook/Flashcards.tsx
git commit -m "feat(flashcards): a deck library, and a card list that is also the editor"
```

---

## Task 14: Review — the scheduler of record

**Files:**
- Create: `web/components/notebook/flashcards/CardFace.tsx`, `ActivityChrome.tsx`, `ReviewSession.tsx`
- Create: `web/app/(dashboard)/notebook/flashcards/[deckId]/review/page.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: `useDueCards`, `gradeFlashcard` (both now carrying `preview` and `hint`).
- Produces: `<CardFace text hint side>`, `<ActivityChrome title progress onExit>`.

- [ ] **Step 1: Port the accessibility contract into `CardFace`**

Faces stay `<div>`s with **no `aria-label`**, the inactive face keeps `aria-hidden` + `inert`, and the flip is a separate `<button>` beneath them — for the three reasons recorded on 2026-08-28: an `aria-label` overrides descendant content and would have a screen reader read LaTeX source; a markdown link inside a `<button>` is invalid HTML that Firefox mis-activates; and both faces stay mounted under `backface-visibility`, so both were focusable. Keep `data-testid` `flashcard-front` / `flashcard-back` / `flashcard-inner` / `flashcard-flip` — the e2e addresses them, and a visibility assertion cannot see a 3D flip.

- [ ] **Step 2: Build `ReviewSession`**

Keyboard: `Space`/`Enter` flips, `1`–`4` grade, `U` undoes the last grade, `Esc` exits. Each grade button renders its real interval from `card.preview` (`GOOD · 4d`). Grading advances optimistically and reconciles from the response. The session ends on a summary: reviewed, again-rate, elapsed, when the next card is due.

- [ ] **Step 3: Write the e2e**

Create a deck and two cards through the API inside the test, then: flip with `Space`, assert both faces typeset (`.katex-mathml math`, not `.katex` — a regression to HTML-only output looks identical on screen and is silently unreadable to assistive tech), grade with `3`, and assert the due count drops and the card's state persists across a reload.

- [ ] **Step 4: Verify and commit**

```bash
cd web && npx playwright test notebook.spec.ts
git add web/components/notebook/flashcards "web/app/(dashboard)/notebook/flashcards" web/e2e/notebook.spec.ts
git commit -m "feat(flashcards): a review session that says what each grade will cost"
```

---

## Task 15: Browse — cram without consequences

**Files:**
- Create: `web/components/notebook/flashcards/BrowseSession.tsx`
- Create: `web/app/(dashboard)/notebook/flashcards/[deckId]/cards/page.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: `useDeck` (Task 10), `setStarred` (Task 10), `<CardFace>` (Task 14).
- Produces: nothing.

- [ ] **Step 1: Build it**

Large card, click or `Space` to flip, `←`/`→` to move, `n / N` counter, shuffle, star toggle (persisted — starring is a property of the card, not of the session). A `TRACK PROGRESS` toggle swaps `←`/`→` for `✗`/`✓` plus an undo, exactly as Quizlet does; the two piles are **`useState`, never posted**. When the deck ends with a non-empty `✗` pile, offer a second round over just those.

- [ ] **Step 2: Write the e2e that guards the exemption**

```ts
test("browsing a deck does not touch its schedule", async ({ page, request }) => {
  // The reason Browse and Review both exist: skimming a deck before a lecture
  // must not rewrite a schedule built over weeks.
  const before = await (await request.get("/api/flashcards/due-summary")).json();

  await page.goto(`/notebook/flashcards/${deckId}/cards`);
  await page.getByLabel("Track progress").click();
  await page.getByTestId("flashcard-flip").click();
  await page.getByRole("button", { name: "Know it" }).click();

  const after = await (await request.get("/api/flashcards/due-summary")).json();
  expect(after.total).toBe(before.total);
});
```

- [ ] **Step 3: Verify and commit**

```bash
cd web && npx playwright test notebook.spec.ts
git add web/components/notebook/flashcards/BrowseSession.tsx "web/app/(dashboard)/notebook/flashcards" web/e2e/notebook.spec.ts
git commit -m "feat(flashcards): browse a deck without spending its schedule"
```

---

## Task 16: Learn — adaptive, and the logic that makes it fair

**Files:**
- Create: `web/lib/flashcards/matching.ts`, `matching.test.ts`, `distractors.ts`, `distractors.test.ts`
- Create: `web/components/notebook/flashcards/LearnSession.tsx`
- Create: `web/app/(dashboard)/notebook/flashcards/[deckId]/learn/page.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: `useDeck`, `gradeFlashcard`.
- Produces:
  - `judge(expected: string, actual: string): "correct" | "close" | "wrong"`
  - `pickDistractors(cards: Card[], correct: Card, count: number): Card[]`

- [ ] **Step 1: Write the failing matcher tests**

```ts
import { describe, expect, it } from "vitest";
import { judge } from "./matching";

describe("judge", () => {
  it("accepts an exact answer", () => expect(judge("Paris", "Paris")).toBe("correct"));

  it("ignores case, surrounding space and terminal punctuation", () => {
    expect(judge("Paris", "  paris ")).toBe("correct");
    expect(judge("Paris", "paris.")).toBe("correct");
  });

  it("ignores diacritics, because a learner may have no way to type them", () => {
    expect(judge("café", "cafe")).toBe("correct");
  });

  it("collapses internal whitespace", () => {
    expect(judge("big O notation", "big   O    notation")).toBe("correct");
  });

  it("calls a near miss close rather than wrong", () => {
    // One transposed letter is a typo, not ignorance. Graded `hard`.
    expect(judge("mitochondria", "mitochondira")).toBe("close");
  });

  it("calls a genuinely different answer wrong", () => {
    expect(judge("mitochondria", "ribosome")).toBe("wrong");
  });

  it("does not let a short answer pass on similarity alone", () => {
    // "cat" vs "bat" is 0.67 — below threshold, and it must stay that way:
    // short answers are where a lenient matcher does the most damage.
    expect(judge("cat", "bat")).toBe("wrong");
  });

  it("treats an empty answer as wrong, never as close", () => {
    expect(judge("Paris", "")).toBe("wrong");
    expect(judge("Paris", "   ")).toBe("wrong");
  });
});
```

- [ ] **Step 2: Implement `judge`**

Normalise (lowercase, NFD-strip diacritics, collapse whitespace, drop terminal punctuation), compare for equality, then compute a normalised Levenshtein similarity; `>= 0.85` is `close`, below is `wrong`. An empty normalised answer short-circuits to `wrong` before any similarity is computed.

- [ ] **Step 3: Write the failing distractor tests**

Cover: never returns the correct card; returns `count` when the deck is large enough; returns fewer, without repeating, when the deck has fewer cards than asked for; a two-card deck yields exactly one distractor; a one-card deck yields none, which the caller must treat as "no multiple choice available".

- [ ] **Step 4: Implement, then build `LearnSession`**

Rounds of 7. Per card, the question type escalates with in-session mastery: multiple choice → typed answer → flip-confirm. Falls back to typed answer when `pickDistractors` cannot supply three. Outcome mapping, posted once per card: first-attempt correct → `good`; `close`, a second attempt, a revealed hint, or the "I was right" override → `hard`; wrong → `again`. The override never yields `good` — a card you had to argue for is not a card you knew.

- [ ] **Step 5: Verify and commit**

```bash
cd web && npm run test:unit && npx playwright test notebook.spec.ts
git add web/lib/flashcards web/components/notebook/flashcards/LearnSession.tsx "web/app/(dashboard)/notebook/flashcards" web/e2e/notebook.spec.ts
git commit -m "feat(flashcards): an adaptive learn mode that feeds the same schedule"
```

---

## Task 17: Match — the game

**Files:**
- Create: `web/components/notebook/flashcards/MatchGame.tsx`
- Create: `web/app/(dashboard)/notebook/flashcards/[deckId]/match/page.tsx`
- Modify: `web/e2e/notebook.spec.ts`

**Interfaces:**
- Consumes: `useDeck`, `postMatchScore`, `useMatchBest` (Task 10).
- Produces: nothing.

- [ ] **Step 1: Build it**

Six pairs sampled from the deck, twelve tiles shuffled. Click a term, then its definition: a correct pair clears, a wrong one flashes and resets the selection. **Click-to-pair, not drag** — drag is hostile to Playwright and worse on touch, and buys nothing here. A timer runs from the first click; finishing posts the elapsed time and shows it against the personal best. A deck with fewer than six cards plays with what it has; a deck with fewer than two shows an honest empty state rather than an unplayable board.

- [ ] **Step 2: Write the e2e**

Create a four-card deck in the test, play it out by reading tile text from the DOM and clicking the true pairs, and assert a best time appears afterwards — and that `/api/flashcards/due-summary` is unchanged, because Match must not touch the schedule either.

- [ ] **Step 3: Verify and commit**

```bash
cd web && npx playwright test notebook.spec.ts
git add web/components/notebook/flashcards/MatchGame.tsx "web/app/(dashboard)/notebook/flashcards" web/e2e/notebook.spec.ts
git commit -m "feat(flashcards): match, for the deck you would rather play than grade"
```

---

## Task 18: The Course Hub tells the truth again

**Files:**
- Modify: `web/components/notebook/CourseHub.tsx:296-316`
- Modify: `web/e2e/notebook.spec.ts:237-240`

- [ ] **Step 1: Rewire the STUDIO deck button**

Point it at `POST /api/flashcards/decks/generate` with `sources: paths`, `track()` the returned job id, and **delete** `note="reads flashcards.md · ignores the selection"` — it stops being true, because the generator now reads the same corpus the guide does. The label gains the same `${scopeNote}` the other two carry.

- [ ] **Step 2: Update the assertion that guarded the old behaviour**

`notebook.spec.ts:240` asserts that note is visible. Replace it with the opposite claim — the deck button now shows a source count like its siblings — so the test tracks the honest behaviour rather than the historical one.

- [ ] **Step 3: Repair the test stranded by the FSRS rewrite**

`study.spec.ts:28` (now `notebook.spec.ts`) has asserted mock card content against a `flashcards.md` fixture that never existed. With real deck creation it can build its own deck through the API — **inside the test**, never seeded at startup.

- [ ] **Step 4: Verify and commit**

```bash
cd web && npx playwright test notebook.spec.ts
git add web/components/notebook/CourseHub.tsx web/e2e/notebook.spec.ts
git commit -m "fix(notebook): the deck button honours the source selection, and says so"
```

---

## Task 19: Documentation

**Files:**
- Create: `docs/notebook.md`
- Modify: `README.md`, `CHANGELOG.md`, `web/lib/flags.ts`

- [ ] **Step 1: Write `docs/notebook.md`**

Following `docs/notes-relationships.md`'s shape: what the Notebook is, how to pop it out and what the second window can and cannot do, the four ways cards get in (with the paste delimiters spelled out), what each of the four activities does to the schedule and what it deliberately does not, and how to export a deck back to markdown.

- [ ] **Step 2: Update `README.md` and `CHANGELOG.md`**

Rename Study to Notebook wherever it appears; add an Unreleased CHANGELOG entry covering the three problems this branch closes.

- [ ] **Step 3: Update the flag comment**

`web/lib/flags.ts`'s `flashcards` comment still describes deck generation as parsing `flashcards.md`. Rewrite it to describe what is now true.

- [ ] **Step 4: Commit**

```bash
git add docs/notebook.md README.md CHANGELOG.md web/lib/flags.ts
git commit -m "docs(notebook): what the notebook is, and how its flashcards work"
```

---

## Task 20: Full-suite verification

- [ ] **Step 1: Run every gate CI runs**

```bash
ruff check .
.venv/Scripts/python -m pytest -q
.venv/Scripts/python desktop/tests/smoke_backend.py --target desktop/backend/argus_server.py
node --test desktop/tests/
cd web && npx tsc --noEmit && npm run lint && npm run test:unit && npm run build && npm run e2e
node desktop/scripts/check-versions.mjs
```

Every one must pass. A failure that predates this branch must be **proven** pre-existing by running the same command in a worktree at `df513b5` with `node_modules` junctioned in — not asserted.

- [ ] **Step 2: Manual pass CI cannot reach**

Stage the desktop shell (`cd desktop && npm run stage`), launch it, pop the Notebook out of the **packaged** app, and confirm API calls resolve. The `additionalArguments` inheritance risk from Task 6 is invisible in dev, because dev is same-origin through the Next rewrite.

- [ ] **Step 3: Push and open the pull request**

```bash
git push -u origin feature/notebook-flashcards
```

`gh` is not installed on this machine, so open the PR from the URL git prints on push.

---

## Self-review notes

Checked against the spec:

- **Spec coverage.** P1 → Tasks 1–3. P2 rename → Task 4; window → Tasks 5–7. P3 schema/migration → Task 8; store/parsing/scheduler → Task 9; endpoints → Task 10; vault import/export → Task 11; generation → Task 12; library and editor → Task 13; the four activities → Tasks 14–17; the Course Hub correction → Task 18; docs → Task 19. Vitest, promised in the spec's testing section, is Task 1.
- **Type consistency.** `card_ref` is the review join key in Tasks 8, 9, 10 and 11. `judge` returns `correct | close | wrong` in both Task 16's test and its implementation. `reconcile` has one signature across Tasks 1 and 2. `isNotebookUrl(url, origin)` matches between Task 6's test and `main.js`.
- **Known ordering constraint.** Task 12 changes `build_flashcards_router`'s signature, so `backend/main.py:254` must change in the same commit or the app fails to boot.
