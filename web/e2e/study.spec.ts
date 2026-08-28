import { expect, test } from "@playwright/test";

test("study sub-nav deep-links between overview, flashcards, and exam", async ({ page }) => {
  await page.goto("/study");
  await expect(page.getByRole("tab", { name: "OVERVIEW" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("▍COURSES")).toBeVisible(); // exact panel eyebrow — "COURSES" alone matches 3 nodes
  // Seeded vault course. `.first()`: "CS000" appears twice since Phase H —
  // the course card AND the shared ingest dropzone's upload-target <option>.
  await expect(page.getByText("CS000").first()).toBeVisible();

  await page.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(page).toHaveURL(/\/study\/flashcards$/);
  await expect(page.getByText("DECK.MANAGE")).toBeVisible();
  await expect(page.getByText("STUDY.SESSION")).toBeVisible();

  await page.getByRole("tab", { name: "PRACTICE EXAM" }).click();
  await expect(page).toHaveURL(/\/study\/exam$/);
  await expect(page.getByText("PRACTICE.EXAM")).toBeVisible();
  await expect(page.getByText("SCORES.HISTORY")).toBeVisible();

  // Deep link directly to a sub-page and back to overview.
  await page.goto("/study/flashcards");
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "OVERVIEW" }).click();
  await expect(page).toHaveURL(/\/study$/);
});

test("flashcard flip and grading advance the mock study session", async ({ page }) => {
  await page.goto("/study/flashcards");
  // Named explicitly rather than relying on which deck lands first in the
  // list: there is now a second, notation-carrying deck beside this one.
  await page.getByRole("button", { name: /CS000 flashcards/ }).click();
  const front = page.getByTestId("flashcard-front");
  await expect(front).toContainText("What is Big-O of binary search?");
  // Both faces stay mounted (CSS backface-visibility, not display:none), so
  // flip state is asserted structurally via the inner wrapper's class, not
  // text visibility — Playwright's visibility check doesn't model 3D backfaces.
  const inner = page.getByTestId("flashcard-inner");
  await expect(inner).not.toHaveClass(/is-flipped/);
  // The faces are no longer buttons — they render markdown, and an <a> inside
  // a <button> is invalid while an aria-label built from the raw card text
  // would have a screen reader read LaTeX source. The flip is its own control.
  const flip = page.getByTestId("flashcard-flip");
  await expect(flip).toHaveAttribute("aria-pressed", "false");
  await flip.click();
  await expect(inner).toHaveClass(/is-flipped/);
  await expect(flip).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "GOOD" }).click();
  // Real wording (Flashcards.tsx): "scheduled :: <grade> — next due <date>" —
  // the exact due date is FSRS-computed and locale-formatted, so only the
  // fixed prefix is asserted.
  await expect(page.getByText(/scheduled :: good — next due/)).toBeVisible();
});

test("a flashcard carrying notation is typeset on both faces", async ({ page }) => {
  await page.goto("/study/flashcards");

  // Its own deck, selected by name. The suite runs with workers: 1 against one
  // shared vault, so the test above grades its card out of the due queue for
  // everything that follows — a notation card behind it in the same deck would
  // be reachable or not depending on test order.
  await page.getByRole("button", { name: /CS000 notation/ }).click();
  const front = page.getByTestId("flashcard-front");

  // `.katex-mathml math` rather than `.katex`: that node exists only under
  // `output: "htmlAndMathml"`, so a regression to `output: "html"` — which
  // looks identical and is silently unreadable to a screen reader — fails
  // here rather than shipping.
  await expect(front.locator(".katex-mathml math").first()).toBeAttached();
  await expect(page.getByTestId("flashcard-back").locator(".katex").first()).toBeAttached();
  // The delimiters are gone and nothing failed to parse.
  await expect(page.getByText("$\\nabla")).toHaveCount(0);
  await expect(page.locator(".katex-error")).toHaveCount(0);
});

test("course hub opens from a course row and links back", async ({ page }) => {
  await page.goto("/study");
  await page.getByRole("link", { name: "HUB →" }).click();
  await expect(page).toHaveURL(/\/study\/course\/CS000$/);
  await expect(page.getByText("COURSE.HUB · CS000")).toBeVisible();
  // "Sample Course" is ambiguous here by design: course.md (the hub note
  // itself, title "Sample Course") lives inside 15-Courses/CS000/ and so
  // also shows up as a SOURCES row — scope to the header to pick the title.
  await expect(page.locator("header").getByText("Sample Course")).toBeVisible();

  await page.getByRole("button", { name: "← BACK" }).click();
  await expect(page).toHaveURL(/\/study$/);
});

// Regression test for the reported bug: "Study … still retains sample data
// after it is deleted." The × button used to only hide the row in local
// React state — the course came right back on the next reload, route
// change, or app restart. The reload below is the whole point of this test.
test("deleting a course removes it permanently, even after a reload", async ({ page }) => {
  await page.goto("/study");

  await page.getByRole("button", { name: "+ ADD COURSE" }).click();
  await page.getByLabel("Course code").fill("CS999");
  await page.getByLabel("Course name").fill("Delete Me");
  await page.getByRole("button", { name: "ADD", exact: true }).click();
  await expect(page.getByText("CS999").first()).toBeVisible();

  await page.getByRole("button", { name: "Delete CS999" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Delete CS999" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "DELETE" }).click();
  await expect(page.getByText("CS999").first()).not.toBeVisible();

  await page.reload();
  await expect(page.getByText("CS999").first()).not.toBeVisible();
});

// Regression test for the reported bug: "Study — ingesting files seems to
// not work." IngestPanel's upload target used to be the course *root*
// (`15-Courses/<code>`), not the real `materials/` folder the backend
// reports via `CourseInfo.materials_path` — the file saved fine, but
// courses() only ever counts files inside materials/, so the row stayed at
// "0 materials" and GUIDE/EXAM never left disabled. CS000 (seeded by
// start-backend.mjs) starts with zero materials, so GUIDE starting disabled
// and becoming enabled after one upload is the whole proof.
test("uploading through the shared ingest panel lands in materials/ and unlocks GUIDE/EXAM", async ({
  page,
}) => {
  await page.goto("/study");

  const guideButton = page.getByRole("button", { name: "GUIDE" });
  await expect(guideButton).toBeDisabled();

  await page.getByLabel("upload target").selectOption("CS000");
  // Two file inputs exist on this page: CS000's own row-level "+ FILES"
  // input (CoursesPanel) and the shared ingest dropzone's — this is the
  // latter, the one actually driven by the "upload target" selector above.
  await page.locator('input[type="file"]').last().setInputFiles({
    name: "syllabus.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake pdf bytes"),
  });

  await expect(page.getByText(/done :: syllabus\.pdf indexed|saved/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(guideButton).toBeEnabled();
  await expect(page.getByRole("button", { name: "+ EXAM" })).toBeEnabled();
});

// These run against the real backend, so the generator is a live provider
// call. Every ingest below therefore leaves the note style on "don't write a
// note" — the save/index/progress path is what the Course Hub gained, and
// exercising the note would make the suite depend on a live model. Same
// constraint sources.spec.ts documents.

test("the course hub ingests into materials/ and reports every stage in place", async ({
  page,
}) => {
  await page.goto("/study/course/CS000");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  await expect(sources).toBeVisible();

  await sources.getByRole("button", { name: "+ INGEST" }).click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();
  // The destination is pinned to the course, so there is no picker to set.
  await expect(dialog.getByText("15-Courses/CS000/materials")).toBeVisible();
  await expect(dialog.getByLabel("Save to")).toHaveCount(0);

  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-hub-lecture.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Hub Lecture\n\nA fact only this file knows.\n"),
  });
  await expect(dialog.getByText("e2e-hub-lecture.md")).toBeVisible();
  await dialog.getByRole("button", { name: /^Ingest/ }).click();
  await expect(dialog).toBeHidden();

  // Progress renders inside the rail — the file is named and its outcome
  // reported, where the old dropzone said "uploading…" and nothing else.
  await expect(sources.getByText("e2e-hub-lecture.md")).toBeVisible();
  await expect(sources.getByText(/\d+ chunks|no chunks/)).toBeVisible({ timeout: 30_000 });

  // …and it becomes a real, selected source under the materials zone.
  await expect(
    sources.getByRole("checkbox", { name: /Use e2e-hub-lecture as a source/ }),
  ).toHaveAttribute("aria-checked", "true", { timeout: 30_000 });
});

test("unticking a source sticks across a reload and is counted everywhere", async ({ page }) => {
  await page.goto("/study/course/CS000");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  // course.md is seeded by start-backend.mjs, so the rail is never empty.
  const box = sources.getByRole("checkbox").first();
  await expect(box).toHaveAttribute("aria-checked", "true");
  const label = (await box.getAttribute("aria-label")) ?? "";

  const before = await sources.getByRole("checkbox", { checked: true }).count();
  await box.click();
  await expect(box).toHaveAttribute("aria-checked", "false");
  await expect(page.getByText(`SOURCES · ${before - 1}/`)).toBeVisible();

  // The whole point: a selection is a working set, so it survives coming back
  // to the same course tomorrow.
  await page.reload();
  const reloaded = sources.getByRole("checkbox", { name: label });
  await expect(reloaded).toHaveAttribute("aria-checked", "false", { timeout: 15_000 });

  // Chat states the scope, so a narrowed hub does not look like a thin index.
  await expect(page.getByText(`sources :: ${before - 1}/`)).toBeVisible();

  await sources.getByRole("button", { name: "ALL" }).click();
  await expect(reloaded).toHaveAttribute("aria-checked", "true");
});

test("selecting nothing disables the generators rather than widening them", async ({ page }) => {
  await page.goto("/study/course/CS000");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  await sources.getByRole("button", { name: "NONE" }).click();

  await expect(sources.getByText("Nothing selected")).toBeVisible();
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await expect(studio.getByRole("button", { name: /^study guide/ })).toBeDisabled();
  await expect(studio.getByRole("button", { name: /^practice exam/ })).toBeDisabled();
  // Decks read flashcards.md, not the corpus, so the selection does not
  // apply to them — and the button says so instead of going dark.
  await expect(studio.getByRole("button", { name: "flashcard deck" })).toBeEnabled();
  await expect(studio.getByText("reads flashcards.md · ignores the selection")).toBeVisible();

  await sources.getByRole("button", { name: "ALL" }).click();
  await expect(studio.getByRole("button", { name: /^study guide/ })).toBeEnabled();
});

/**
 * The two below are the assertions the UX audit named as missing: the suite
 * exercised the plumbing thoroughly and the *agreements between components*
 * not at all, which is how both defects shipped green.
 */

/** Put a uniquely named file in the rail, so neither test below depends on
 * what an earlier one happened to ingest. Note style stays empty: e2e runs the
 * real backend, so asking for a note would be a live model call. */
async function ingestProbe(page: import("@playwright/test").Page, name: string) {
  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  // Wait for the course itself to load before opening the dialog. The hub
  // pins the destination from `GET /api/study/courses`, and a dialog opened
  // before that resolves has no `lockedTarget` — it falls back to the default
  // inbox and keeps it, because `target` is seeded once and the reconciling
  // effect bails whenever `lockedTarget` is set. The dialog then *shows* the
  // course as pinned and POSTs the inbox.
  await expect(page.getByText("Sample Course")).toBeVisible({ timeout: 15_000 });
  await sources.getByRole("button", { name: "+ INGEST" }).click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: `${name}.md`,
    mimeType: "text/markdown",
    buffer: Buffer.from(`# ${name}

A fact only this file knows.
`),
  });
  // Pinned to the course, so there is no picker — and the file must land in
  // the course, not wherever the dialog defaulted to.
  await expect(dialog.getByLabel("Save to")).toHaveCount(0);
  await dialog.getByRole("button", { name: /^Ingest/ }).click();
  await expect(dialog).toBeHidden();
  await expect(sources.getByText("into 15-Courses/CS000/materials")).toBeVisible();
  await expect(
    sources.getByRole("checkbox", { name: new RegExp(`Use ${name} as a source`) }),
  ).toBeVisible({ timeout: 30_000 });
}

test("a course opened for the first time has everything selected, not nothing", async ({
  page,
}) => {
  // The regression this pins: the reconcile effect's `setSelected` updater is
  // lazy, so the separate `[code]` reset effect's plain empty set replaced its
  // result — but the updater had already populated `known` as a side effect,
  // so every later reconcile took the "add only unseen paths" branch and found
  // none. The selection was pinned empty permanently and every generative
  // surface in the hub was inert on arrival, which is the exact opposite of
  // what courseSelection.tsx's own docstring promises.
  //
  // It has to be asserted against a file that existed *before* the provider
  // mounted. A newly ingested path is absent from `known`, so the broken
  // reconcile added it anyway — which is exactly why the ingest test above
  // passed all along and this defect still shipped. Hence: ingest, reload,
  // then look.
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto("/study");
  await page.getByRole("link", { name: "HUB →" }).click();
  await ingestProbe(page, "e2e-fresh-mount");

  // Leave and come back *client-side*, so the hub remounts with SWR's cache
  // already warm. That is the commit where the race bit: `data` present the
  // first time both effects run, so the reset's plain empty set landed after
  // the reconcile's lazy updater had already populated `known`. A hard reload
  // never reproduces it — the fetch is still in flight on mount, so the reset
  // runs harmlessly before there is any data to reconcile.
  await page.getByRole("button", { name: "← BACK" }).click();
  await expect(page).toHaveURL(/\/study$/);
  await page.getByRole("link", { name: "HUB →" }).click();
  await expect(page).toHaveURL(/\/study\/course\/CS000$/);

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  const boxes = sources.getByRole("checkbox");
  await expect(boxes.first()).toBeVisible({ timeout: 15_000 });

  // Counted rather than hard-coded: the rail's contents depend on what earlier
  // tests ingested, but "all of them" is the invariant either way.
  const total = await boxes.count();
  expect(total).toBeGreaterThan(0);
  await expect(sources.getByRole("checkbox", { checked: false })).toHaveCount(0);
  await expect(page.getByText(`SOURCES · ${total}/${total} selected`)).toBeVisible();
});

test("ALL under a filter selects what is on screen, not what the filter hides", async ({
  page,
}) => {
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto("/study/course/CS000");
  await ingestProbe(page, "e2e-filter-probe");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  const total = await sources.getByRole("checkbox").count();
  expect(total).toBeGreaterThan(1);

  // Start from nothing, so the count after ALL can only have come from ALL.
  await sources.getByRole("button", { name: "NONE" }).click();
  await expect(page.getByText(`SOURCES · 0/${total} selected`)).toBeVisible();

  await sources.getByLabel("Filter sources").fill("e2e-filter-probe");
  const shown = await sources.getByRole("checkbox").count();
  expect(shown).toBeGreaterThan(0);
  expect(shown).toBeLessThan(total);

  // The defect: one row on screen, a button labelled ALL, and every file in
  // the course silently scoped into chat and both generators — then persisted,
  // so the reload that might have revealed it did not.
  await sources.getByRole("button", { name: `ALL (${shown})` }).click();
  await expect(page.getByText(`SOURCES · ${shown}/${total} selected`)).toBeVisible();

  // The escape hatch is named rather than being what the plain button did.
  await sources.getByRole("button", { name: `Select all ${total} in this course` }).click();
  await expect(page.getByText(`SOURCES · ${total}/${total} selected`)).toBeVisible();
});
