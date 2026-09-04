import { expect, test } from "@playwright/test";

test("study sub-nav deep-links between overview, flashcards, and exam", async ({ page }) => {
  await page.goto("/notebook");
  await expect(page.getByRole("tab", { name: "OVERVIEW" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("▍COURSES")).toBeVisible(); // exact panel eyebrow — "COURSES" alone matches 3 nodes
  // Seeded vault course. `.first()`: "CS000" appears twice since Phase H —
  // the course card AND the shared ingest dropzone's upload-target <option>.
  await expect(page.getByText("CS000").first()).toBeVisible();

  await page.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(page).toHaveURL(/\/notebook\/flashcards$/);
  await expect(page.getByText("▍DECKS")).toBeVisible();

  await page.getByRole("tab", { name: "PRACTICE EXAM" }).click();
  await expect(page).toHaveURL(/\/notebook\/exam$/);
  await expect(page.getByText("PRACTICE.EXAM")).toBeVisible();
  await expect(page.getByText("SCORES.HISTORY")).toBeVisible();

  // Deep link directly to a sub-page and back to overview.
  await page.goto("/notebook/flashcards");
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "OVERVIEW" }).click();
  await expect(page).toHaveURL(/\/notebook$/);
});

test("a review session flips, grades, and says what each grade will cost", async ({ page }) => {
  await page.goto("/notebook/flashcards");
  // Named explicitly rather than relying on list order: there is a second,
  // notation-carrying deck beside this one.
  await page.getByRole("link", { name: /CS000 flashcards/ }).click();
  await expect(page).toHaveURL(/\/notebook\/flashcards\/\d+$/);

  await page.getByRole("link", { name: /^REVIEW/ }).click();
  await expect(page).toHaveURL(/\/review$/);

  const front = page.getByTestId("flashcard-front");
  await expect(front).toContainText("What is Big-O of binary search?");

  // Both faces stay mounted (CSS backface-visibility, not display:none), so
  // flip state is asserted structurally via the inner wrapper's class, not
  // text visibility — Playwright's visibility check doesn't model 3D backfaces.
  const inner = page.getByTestId("flashcard-inner");
  await expect(inner).not.toHaveClass(/is-flipped/);

  // The faces are not buttons — they render markdown, and an <a> inside a
  // <button> is invalid while an aria-label built from the raw card text would
  // have a screen reader read LaTeX source. The flip is its own control.
  const flip = page.getByTestId("flashcard-flip");
  await expect(flip).toHaveAttribute("aria-pressed", "false");
  await flip.click();
  await expect(inner).toHaveClass(/is-flipped/);
  await expect(flip).toHaveAttribute("aria-pressed", "true");

  // Every grade button carries its real next interval, computed by FSRS
  // without committing. Four unlabelled verbs asked the user to guess.
  const good = page.getByRole("button", { name: /3 · good/ });
  await expect(good).toContainText(/\d+[mhdy]|mo/);

  await good.click();
  // The toast reports the same interval the button promised — they come out of
  // one function, so a drift between them is a bug rather than a rounding.
  await expect(page.getByText(/good :: back in /)).toBeVisible();
  await expect(page.getByText("session complete")).toBeVisible();
});

test("space flips and a number grades, without reaching for the mouse", async ({
  page,
  request,
}) => {
  // Its own deck, built here. This test *grades*, and grading spends a card
  // out of the due queue for every later test -- the suite runs workers: 1
  // against one shared vault, so borrowing a seeded deck would decide whether
  // the notation test below can see its fixture.
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e keyboard deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: [{ front: "keyboard front", back: "keyboard back" }] },
  });

  // A review session is dozens of identical interactions in a row, which is
  // exactly the shape that should never require a pointer.
  await page.goto(`/notebook/flashcards/${deck.id}/review`);

  const inner = page.getByTestId("flashcard-inner");
  // Wait for the card before pressing anything: this asserts how keys are
  // handled, not how fast the queue loads, and a keypress into a page that has
  // not hydrated tests neither.
  await expect(page.getByTestId("flashcard-front")).toBeAttached();

  await page.keyboard.press("Space");
  await expect(inner).toHaveClass(/is-flipped/);
  await page.keyboard.press("Space");
  await expect(inner).not.toHaveClass(/is-flipped/);

  // A number before the answer is showing flips instead of grading: grading a
  // card you have not seen the back of is almost always a misfire.
  await page.keyboard.press("3");
  await expect(inner).toHaveClass(/is-flipped/);
  await page.keyboard.press("3");
  await expect(page.getByText("session complete")).toBeVisible();
});

test("a flashcard carrying notation is typeset on both faces", async ({ page }) => {
  await page.goto("/notebook/flashcards");
  await page.getByRole("link", { name: /CS000 notation/ }).click();

  // Its own deck, reached by name. The suite runs with workers: 1 against one
  // shared vault, so an earlier test grading a card out of the queue must not
  // decide whether this one can see its fixture.
  await page.getByRole("link", { name: /^REVIEW/ }).click();
  const front = page.getByTestId("flashcard-front");

  // `.katex-mathml math` rather than `.katex`: that node exists only under
  // `output: "htmlAndMathml"`, so a regression to `output: "html"` — which
  // looks identical and is silently unreadable to a screen reader — fails
  // here rather than shipping.
  await expect(front.locator(".katex-mathml math").first()).toBeAttached();
  await expect(page.getByTestId("flashcard-back").locator(".katex").first()).toBeAttached();
  // The delimiters are gone and nothing failed to parse.
  await expect(page.getByText("$\nabla")).toHaveCount(0);
  await expect(page.locator(".katex-error")).toHaveCount(0);
});

test("course hub opens from a course row and links back", async ({ page }) => {
  await page.goto("/notebook");
  await page.getByRole("link", { name: "HUB →" }).click();
  await expect(page).toHaveURL(/\/notebook\/course\/CS000$/);
  await expect(page.getByText("COURSE.HUB · CS000")).toBeVisible();
  // "Sample Course" is ambiguous here by design: course.md (the hub note
  // itself, title "Sample Course") lives inside 15-Courses/CS000/ and so
  // also shows up as a SOURCES row — scope to the header to pick the title.
  await expect(page.locator("header").getByText("Sample Course")).toBeVisible();

  await page.getByRole("button", { name: "← BACK" }).click();
  await expect(page).toHaveURL(/\/notebook$/);
});

// Regression test for the reported bug: "Study … still retains sample data
// after it is deleted." The × button used to only hide the row in local
// React state — the course came right back on the next reload, route
// change, or app restart. The reload below is the whole point of this test.
test("deleting a course removes it permanently, even after a reload", async ({ page }) => {
  await page.goto("/notebook");

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
  await page.goto("/notebook");

  const guideButton = page.getByRole("button", { name: "GUIDE" });
  await expect(guideButton).toBeDisabled();

  await page.getByLabel("upload target").selectOption("CS000");

  // The dropzone opens the shared dialog now rather than posting to the
  // single-file `POST /api/ingest` with no destination choice, no note style
  // and no progress. Pinned to the selected course, so there is no picker.
  await page.getByRole("button", { name: /drop a file, or click to choose/ }).click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Save to")).toHaveCount(0);

  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "syllabus.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake pdf bytes"),
  });
  await dialog.getByRole("button", { name: /^Ingest/ }).click();
  await expect(dialog).toBeHidden();

  // Per-file progress in the panel, where the old flow had one status line.
  await expect(page.getByText("syllabus.pdf")).toBeVisible({ timeout: 30_000 });

  // The generators unlock only once the job settles -- the parent is told
  // then, not when the upload was accepted.
  await expect(guideButton).toBeEnabled({ timeout: 30_000 });
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
  await page.goto("/notebook/course/CS000");

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
  await page.goto("/notebook/course/CS000");

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
  await page.goto("/notebook/course/CS000");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  await sources.getByRole("button", { name: "NONE" }).click();

  await expect(sources.getByText("Nothing selected")).toBeVisible();
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await expect(studio.getByRole("button", { name: /^study guide/ })).toBeDisabled();
  await expect(studio.getByRole("button", { name: /^practice exam/ })).toBeDisabled();
  // Decks are generated from the same corpus now, so the deck button obeys
  // the selection like its siblings rather than carrying an apology for
  // ignoring it.
  await expect(studio.getByRole("button", { name: /^flashcard deck/ })).toBeDisabled();
  await expect(studio.getByText("reads flashcards.md · ignores the selection")).toHaveCount(0);

  await sources.getByRole("button", { name: "ALL" }).click();
  await expect(studio.getByRole("button", { name: /^study guide/ })).toBeEnabled();
  await expect(studio.getByRole("button", { name: /^flashcard deck/ })).toBeEnabled();
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
  await page.goto("/notebook");
  await page.getByRole("link", { name: "HUB →" }).click();
  await ingestProbe(page, "e2e-fresh-mount");

  // Leave and come back *client-side*, so the hub remounts with SWR's cache
  // already warm. That is the commit where the race bit: `data` present the
  // first time both effects run, so the reset's plain empty set landed after
  // the reconcile's lazy updater had already populated `known`. A hard reload
  // never reproduces it — the fetch is still in flight on mount, so the reset
  // runs harmlessly before there is any data to reconcile.
  await page.getByRole("button", { name: "← BACK" }).click();
  await expect(page).toHaveURL(/\/notebook$/);
  await page.getByRole("link", { name: "HUB →" }).click();
  await expect(page).toHaveURL(/\/notebook\/course\/CS000$/);

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
  await page.goto("/notebook/course/CS000");
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

/**
 * The source row used to be a 58px `<li>` carrying `hover:border-lineHi` — an
 * interactive-looking row with no handler on it at all — whose only real
 * target was a 14x14px checkbox, under a third of the 44px minimum on both
 * axes. The whole row is the control now, so the target and the affordance
 * finally describe the same element.
 *
 * Asserted on the element that exposes `role=checkbox`, deliberately: the fix
 * is only worth anything if the enlarged target is the *same* node assistive
 * technology and the rest of this file address. Two checkboxes per row, or a
 * label wrapper that swallows the row text into the accessible name, would
 * pass a naive "row is clickable" check and still be a regression.
 */
test("a source row's toggle target is the whole row, not a 14px box", async ({ page }) => {
  await page.goto("/notebook/course/CS000");

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  const box = sources.getByRole("checkbox").first();
  await expect(box).toBeVisible({ timeout: 15_000 });

  const target = await box.boundingBox();
  expect(target).not.toBeNull();
  expect(target!.width, "toggle target is narrower than 44px").toBeGreaterThanOrEqual(44);
  expect(target!.height, "toggle target is shorter than 44px").toBeGreaterThanOrEqual(44);

  // Clicking the row's *text* — the part that was inert before — toggles it.
  // `force`, because the title span is not itself the click target; the point
  // is that the press lands on the checkbox anyway.
  const before = await box.getAttribute("aria-checked");
  await box.locator("span.truncate").first().click({ force: true });
  await expect(box).toHaveAttribute("aria-checked", before === "true" ? "false" : "true");

  // Exactly one checkbox per row, and its name is still the source, not the
  // whole row's text.
  const rowBoxes = sources.locator("li").first().getByRole("checkbox");
  await expect(rowBoxes).toHaveCount(1);
  await expect(rowBoxes).toHaveAttribute("aria-label", /^Use .* as a source$/);
});

test("shift-click ticks a run, and only the rows on screen", async ({ page }) => {
  // Picking lectures 3-8 out of 40 meant eight separate clicks on a 14px box,
  // which at the scale this feature is designed for -- its own comments talk
  // about "3 files out of 200" -- becomes the dominant cost of using it.
  //
  // The hazard the audit names when adding it: a range computed over the full
  // list rather than the visible one re-creates the ALL-under-a-filter bug in
  // a new place. This asserts the range is bounded by what is on screen.
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto("/notebook");
  await page.getByRole("link", { name: "HUB →" }).click();

  // Ingests its own rows so it does not depend on what an earlier spec left
  // behind -- running this file alone, CS000's rail is empty. One job, not
  // three: the store deliberately allows a single ingest at a time, so
  // firing them back to back makes the second and third 409.
  await expect(page.getByText("Sample Course")).toBeVisible({ timeout: 15_000 });
  await page.locator("section").filter({ hasText: "▍SOURCES" })
    .getByRole("button", { name: "+ INGEST" })
    .click();
  const batch = page.getByRole("dialog", { name: "Ingest files" });
  await batch.getByLabel("Write a note from each file").selectOption("");
  await batch.locator('input[type="file"]').setInputFiles(
    ["e2e-run-a", "e2e-run-b", "e2e-run-c"].map((name) => ({
      name: `${name}.md`,
      mimeType: "text/markdown",
      buffer: Buffer.from(`# ${name}`),
    })),
  );
  await batch.getByRole("button", { name: /^Ingest/ }).click();
  await expect(batch).toBeHidden();

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  const boxes = sources.getByRole("checkbox");
  await expect(boxes.first()).toBeVisible({ timeout: 15_000 });
  const total = await boxes.count();
  expect(total).toBeGreaterThanOrEqual(3);

  await sources.getByRole("button", { name: "NONE" }).click();
  await expect(page.getByText(`SOURCES · 0/${total} selected`)).toBeVisible();

  await boxes.first().click();
  await boxes.nth(2).click({ modifiers: ["Shift"] });

  await expect(page.getByText(`SOURCES · 3/${total} selected`)).toBeVisible();
});

test("a generation started in the course hub survives leaving the tab", async ({ page }) => {
  // The reported bug: generation state lived in `busyAction`, a component
  // local, so navigating away discarded the only record the UI had of work the
  // backend was still doing. The guide still landed in the vault; nothing ever
  // said where.
  //
  // Both halves are checked here, and they are the two that were missing:
  // that the request carries `background: true` (the backend has accepted it
  // since the job store was generalised, and nothing sent it), and that the
  // resulting job outlives every navigation.
  //
  // Routed rather than live: `POST /api/study/guide` is a real model call over
  // a real corpus, and the claim under test belongs to the registry, not to
  // the generator. The backend's side of this contract is covered by
  // tests/features/study/test_study_api.py::test_a_background_guide_is_accepted_as_a_job.
  let sentBody: Record<string, unknown> | null = null;
  // The listing is stateful, because the registry is: it adopts any running
  // job it sees. A mock that reported one before the click would (correctly)
  // disable the button this test needs to press.
  let queued = false;

  await page.route("**/api/study/guide", async (route) => {
    sentBody = route.request().postDataJSON();
    queued = true;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-guide" }),
    });
  });

  await page.route("**/api/ingest/jobs?kind=all", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        jobs: queued
          ? [
              {
                id: "e2e-guide",
                created_at: "2026-09-03T00:00:00",
                finished_at: null,
                status: "running",
                kind: "guide",
                params: { course: "CS000" },
                target: "15-Courses/CS000/notebook",
                summary_prompt: "",
                note_style: "",
                total: 1,
                done: 0,
                error: null,
              },
            ]
          : [],
      }),
    });
  });

  await page.goto("/notebook/course/CS000");
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await studio.getByRole("button", { name: /^study guide/ }).click();

  expect(sentBody).toMatchObject({ course: "CS000", background: true });

  // The tray is the proof the job is owned above the router.
  const tray = page.getByRole("button", { name: /Background work: 1 running/ });
  await expect(tray).toBeVisible();

  // Leaving the route it was started from used to be the end of the story.
  // (The Course Hub renders no sub-nav of its own -- it is a workspace, not
  // one of the three tabbed pages -- so this leaves by URL.)
  await page.goto("/notebook/flashcards");
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(tray).toBeVisible();

  // And leaving the mode entirely.
  await page.goto("/dashboard");
  await expect(tray).toBeVisible();
  await tray.click();
  await expect(page.getByText("study guide", { exact: true })).toBeVisible();
});

test("the old study URLs still land on the notebook", async ({ page }) => {
  // Study became Notebook. Every bookmark, obsidian:// deep link and note
  // reference to /study predates the rename, so the redirects are what keep
  // the rename from being a breaking change for the one user who has them.
  await page.goto("/study");
  await expect(page).toHaveURL(/\/notebook$/);
  await expect(page.getByRole("tab", { name: "OVERVIEW" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.goto("/study/flashcards");
  await expect(page).toHaveURL(/\/notebook\/flashcards$/);
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  // Deep paths keep their tail, not just their prefix.
  await page.goto("/study/course/CS000");
  await expect(page).toHaveURL(/\/notebook\/course\/CS000$/);
});

test("the notebook opens in a window of its own", async ({ page, context }) => {
  await page.goto("/notebook");

  const [popup] = await Promise.all([
    context.waitForEvent("page"),
    page.getByRole("button", { name: /pop out/i }).click(),
  ]);
  await popup.waitForLoadState();
  await expect(popup).toHaveURL(/\/notebook\?window=standalone$/);

  // Its own chrome: the page's sub-nav is there, the six-mode strip is not.
  // A window that exists to hold one mode must not offer to navigate out of
  // it -- there would be no way back and no sibling chrome.
  await expect(popup.getByRole("tab", { name: "FLASHCARDS" })).toBeVisible();
  await expect(popup.getByRole("tablist", { name: "Mode" })).toHaveCount(0);

  // And it does not offer to pop itself out again.
  await expect(popup.getByRole("button", { name: /pop out/i })).toHaveCount(0);

  // The flag outlives the query string: it is kept in sessionStorage, which is
  // scoped to this window, so client-side navigation inside it stays
  // standalone while the window that opened it stays ordinary.
  await popup.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(popup).toHaveURL(/\/notebook\/flashcards$/);
  await expect(popup.getByRole("tablist", { name: "Mode" })).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "Mode" })).toBeVisible();

  await popup.close();
});

test("a deck is created, filled by hand, and filled by paste", async ({ page }) => {
  // Everything this test needs, it builds. Nothing is seeded at startup: the
  // suite runs workers: 1 against one shared vault, so a startup fixture is
  // global state that decides other tests' outcomes.
  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: "+ NEW DECK" }).click();
  await page.getByLabel("Deck title").fill("Hand-written deck");
  await page.getByRole("button", { name: "CREATE" }).click();

  await page.getByRole("link", { name: /Hand-written deck/ }).click();
  await expect(page).toHaveURL(/\/notebook\/flashcards\/\d+$/);

  // Typed in.
  await page.getByPlaceholder("the question").fill("capital of France");
  await page.getByPlaceholder("the answer").fill("Paris");
  await page.getByRole("button", { name: "+ ADD CARD" }).click();
  await expect(page.getByLabel("Front of card 1")).toHaveValue("capital of France");

  // Edited in place: the list you browse is the form you edit.
  await page.getByLabel("Back of card 1").fill("Paris, France");
  await page.getByLabel("Back of card 1").blur();
  await expect(page.getByText("1 card", { exact: true })).toBeVisible();

  // Pasted. The preview count comes from the browser twin of the server's
  // parser, so what it promises is what gets created.
  await page.getByRole("button", { name: "IMPORT" }).click();
  const dialog = page.getByRole("dialog", { name: "Import cards" });
  await dialog.getByLabel("Paste rows").fill("ser\tto be\nestar\tto be, temporarily");
  await expect(dialog.getByText("2 cards will be added")).toBeVisible();
  await dialog.getByRole("button", { name: "IMPORT 2" }).click();

  await expect(page.getByText("3 cards", { exact: true })).toBeVisible();
  // Split on the first delimiter only, so a definition keeps its commas.
  await expect(page.getByLabel("Back of card 3")).toHaveValue("to be, temporarily");
});

test("importing a note's Q::/A:: tail fills a deck", async ({ page, request }) => {
  // The fix for the whole feature having been unreachable: ingest writes this
  // shape into every note it generates, and nothing could read it.
  await request.post("/api/note/create", {
    data: {
      path: "50-Reference/e2e-selftest.md",
      content: "# Notes\n\nProse.\n\n## Self-test\n\nQ:: what is P\nA:: polynomial time\n",
    },
  });

  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: "+ NEW DECK" }).click();
  await page.getByLabel("Deck title").fill("Imported deck");
  await page.getByRole("button", { name: "CREATE" }).click();
  await page.getByRole("link", { name: /Imported deck/ }).click();
  // The card count only renders once `useDeck` has resolved, so it is the
  // signal that the page is hydrated and the IMPORT click will be handled.
  await expect(page.getByText("0 cards", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "IMPORT" }).click();
  const dialog = page.getByRole("dialog", { name: "Import cards" });
  await dialog.getByRole("tab", { name: "FROM A NOTE" }).click();
  // Picked from a list, never typed. Typing a path meant already knowing it,
  // spelled exactly, with no listing and no completion.
  await dialog.getByLabel("Search your notes").fill("e2e-selftest");
  await dialog.getByRole("button", { name: /e2e-selftest/ }).click();

  await expect(page.getByText("1 card", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Front of card 1")).toHaveValue("what is P");
});

test("browsing a deck does not touch its schedule", async ({ page, request }) => {
  // The reason Browse and Review both exist: skimming a deck before a lecture
  // must not rewrite a schedule built over weeks. Nothing in this mode posts a
  // grade, and this is the assertion that keeps it that way.
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e browse deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: {
      cards: [
        { front: "browse one", back: "answer one" },
        { front: "browse two", back: "answer two" },
      ],
    },
  });

  const dueBefore = await (await request.get("/api/flashcards/due-summary")).json();

  await page.goto(`/notebook/flashcards/${deck.id}/cards`);
  await expect(page.getByTestId("flashcard-front")).toContainText("browse one");

  // Track progress swaps ←/→ for ✗/✓ — a session-local sort, not a grade.
  await page.getByLabel("Track progress").check();
  await page.getByTestId("flashcard-flip").click();
  await page.getByRole("button", { name: "Know it" }).click();
  await expect(page.getByTestId("flashcard-front")).toContainText("browse two");
  await page.getByRole("button", { name: "Still learning" }).click();

  await expect(page.getByText("1 known · 1 still learning")).toBeVisible();
  await expect(page.getByText("None of this touched your review schedule.")).toBeVisible();

  const dueAfter = await (await request.get("/api/flashcards/due-summary")).json();
  expect(dueAfter.total).toBe(dueBefore.total);

  // The ✗ pile earns a second pass — the reason to sort at all.
  await page.getByRole("button", { name: /REVIEW THE 1 STILL LEARNING/ }).click();
  await expect(page.getByTestId("flashcard-front")).toContainText("browse two");
  await expect(page.getByText("round 2")).toBeVisible();
});

test("starring a card in browse mode persists, because it is not session state", async ({
  page,
  request,
}) => {
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e star deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: [{ front: "star me", back: "starred" }] },
  });

  await page.goto(`/notebook/flashcards/${deck.id}/cards`);
  await page.getByRole("button", { name: "Star this card" }).click();
  await expect(page.getByRole("button", { name: "Unstar this card" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: "Unstar this card" })).toBeVisible();
});

test("learn escalates from choosing to typing, and feeds the same schedule", async ({
  page,
  request,
}) => {
  const ANSWERS: Record<string, string> = {
    "capital of France": "Paris",
    "capital of Japan": "Tokyo",
    "capital of Peru": "Lima",
    "capital of Egypt": "Cairo",
  };
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e learn deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: {
      cards: Object.entries(ANSWERS).map(([front, back]) => ({ front, back })),
    },
  });
  const dueBefore = (await (await request.get("/api/flashcards/due-summary")).json()).total;

  await page.goto(`/notebook/flashcards/${deck.id}/learn`);

  // An unseen card is asked as multiple choice: recognition before recall.
  await expect(page.getByText("choose the answer")).toBeVisible();

  // Deterministically answer WRONG, by reading the prompt and clicking an
  // option that is not its answer. A test that clicks whichever option happens
  // to be first asserts a different thing on every run.
  const prompt = (await page.getByText(/^capital of /).first().innerText()).trim();
  const wrong = Object.values(ANSWERS).find((answer) => answer !== ANSWERS[prompt]);
  await page.getByRole("button", { name: wrong!, exact: true }).click();
  await expect(page.getByText("not quite")).toBeVisible();

  // A wrong answer offers the override, and it promotes to a near miss —
  // never to "I knew that", which would launder a miss into a long interval.
  await page.getByRole("button", { name: "I WAS RIGHT" }).click();
  await expect(page.getByText("counted as a near miss")).toBeVisible();

  await page.getByRole("button", { name: "CONTINUE" }).click();

  // Unlike browse and match, learn does spend reviews: the whole point is that
  // it and REVIEW feed one schedule.
  const dueAfter = (await (await request.get("/api/flashcards/due-summary")).json()).total;
  expect(dueAfter).toBeLessThan(dueBefore);
});

test("a typed answer with a typo is accepted as a near miss", async ({ page, request }) => {
  // One transposed letter is a typing error, not ignorance. Being unfair here
  // teaches the schedule a lie.
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e typing deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: [{ front: "the powerhouse of the cell", back: "mitochondria" }] },
  });

  await page.goto(`/notebook/flashcards/${deck.id}/learn`);
  // A one-card deck cannot pose multiple choice — one distractor is a coin
  // flip and none answers itself — so it falls back to typing.
  await expect(page.getByText("type the answer")).toBeVisible();

  await page.getByLabel("Your answer").fill("mitochondira");
  await page.getByRole("button", { name: "ANSWER" }).click();
  await expect(page.getByText("close enough")).toBeVisible();
});

test("match pairs against the clock and records a best, changing no schedule", async ({
  page,
  request,
}) => {
  const PAIRS: Record<string, string> = {
    "match front one": "match back one",
    "match front two": "match back two",
    "match front three": "match back three",
  };
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e match deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: Object.entries(PAIRS).map(([front, back]) => ({ front, back })) },
  });
  const dueBefore = (await (await request.get("/api/flashcards/due-summary")).json()).total;

  await page.goto(`/notebook/flashcards/${deck.id}/match`);
  await expect(page.getByText("0 / 3 paired")).toBeVisible();

  // Click a term then its definition. Click-to-pair, not drag: two clicks say
  // "these go together" as well as a drag does, and are testable.
  for (const [front, back] of Object.entries(PAIRS)) {
    await page.getByRole("button", { name: front, exact: true }).click();
    await page.getByRole("button", { name: back, exact: true }).click();
  }

  await expect(page.getByText("3 pairs in")).toBeVisible();
  await expect(page.getByText(/best :: /)).toBeVisible();

  // A game must not be able to corrupt weeks of spacing.
  const dueAfter = (await (await request.get("/api/flashcards/due-summary")).json()).total;
  expect(dueAfter).toBe(dueBefore);
});

test("a mispair clears the selection instead of pairing", async ({ page, request }) => {
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e mispair deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: {
      cards: [
        { front: "alpha front", back: "alpha back" },
        { front: "bravo front", back: "bravo back" },
      ],
    },
  });

  await page.goto(`/notebook/flashcards/${deck.id}/match`);
  await page.getByRole("button", { name: "alpha front", exact: true }).click();
  await page.getByRole("button", { name: "bravo back", exact: true }).click();
  await expect(page.getByText("0 / 2 paired")).toBeVisible();

  // ...and the right pair still works afterwards.
  await page.getByRole("button", { name: "alpha front", exact: true }).click();
  await page.getByRole("button", { name: "alpha back", exact: true }).click();
  await expect(page.getByText("1 / 2 paired")).toBeVisible();
});

test("a dropped file becomes cards without touching the vault", async ({ page, request }) => {
  // The zone is a real file input as well as a drop target: a drop-only
  // surface cannot be reached from a keyboard, and setInputFiles exercises the
  // identical handler a drop would.
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e file deck" } })
  ).json();

  await page.goto(`/notebook/flashcards/${deck.id}`);
  await expect(page.getByText("0 cards", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "IMPORT" }).click();

  const dialog = page.getByRole("dialog", { name: "Import cards" });
  await dialog.getByRole("tab", { name: "A FILE" }).click();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "verbs.tsv",
    mimeType: "text/tab-separated-values",
    buffer: Buffer.from("ser\tto be\nestar\tto be, temporarily\n"),
  });

  // The format was guessed, and the count previewed is the count that lands.
  await expect(dialog.getByText("2 cards will be added")).toBeVisible();
  await dialog.getByRole("button", { name: "IMPORT 2" }).click();

  await expect(page.getByText("2 cards", { exact: true })).toBeVisible();
  // Split on the first delimiter only, so a definition keeps its commas.
  await expect(page.getByLabel("Back of card 2")).toHaveValue("to be, temporarily");
});

test("a dropped markdown file is recognised as Q::/A:: rather than delimited", async ({
  page,
  request,
}) => {
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e md deck" } })
  ).json();

  await page.goto(`/notebook/flashcards/${deck.id}`);
  await expect(page.getByText("0 cards", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "IMPORT" }).click();

  const dialog = page.getByRole("dialog", { name: "Import cards" });
  await dialog.getByRole("tab", { name: "A FILE" }).click();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "lecture.md",
    mimeType: "text/markdown",
    // Contains a tab as well, to prove Q:: wins the detection outright: an
    // indented aside is prose, not a second column.
    buffer: Buffer.from("# Lecture\n\n\tan indented aside\n\nQ:: what is P\nA:: polynomial time\n"),
  });

  await expect(dialog.getByLabel("Q:: / A:: pairs")).toBeChecked();
  await expect(dialog.getByText("1 card will be added")).toBeVisible();
  await dialog.getByRole("button", { name: "IMPORT 1" }).click();

  await expect(page.getByLabel("Front of card 1")).toHaveValue("what is P");
});

test("a file that is not text is refused before anything is read", async ({ page, request }) => {
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e reject deck" } })
  ).json();

  await page.goto(`/notebook/flashcards/${deck.id}`);
  await expect(page.getByText("0 cards", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "IMPORT" }).click();

  const dialog = page.getByRole("dialog", { name: "Import cards" });
  await dialog.getByRole("tab", { name: "A FILE" }).click();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "slides.pptx",
    mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    buffer: Buffer.from("not text"),
  });

  await expect(page.getByText(/isn't a text file/)).toBeVisible();
});

test("generation options reach the request, and the deck records them", async ({ page }) => {
  // Routed rather than live: the claim under test is that what the dialog
  // collects is what gets sent. Whether a model then honours it is the
  // backend's contract, covered by tests/features/flashcards/test_generate.py.
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/flashcards/decks/generate", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-deck", deck_id: 999 }),
    });
  });

  await page.goto("/notebook/course/CS000");
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await studio.getByRole("button", { name: /^flashcard deck/ }).click();

  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });
  await dialog.getByRole("button", { name: "HARD" }).click();

  // Start from a known state rather than whatever localStorage remembered.
  for (const style of ["Definition", "Concept", "Cloze", "Application"]) {
    const box = dialog.getByRole("checkbox", { name: new RegExp(style) });
    if (await box.isChecked()) await box.uncheck();
  }
  await dialog.getByRole("checkbox", { name: /Cloze/ }).check();
  await dialog.getByLabel("Your instructions (optional)").fill("Keep answers under ten words.");
  await dialog.getByRole("button", { name: "GENERATE" }).click();

  expect(sent).toMatchObject({
    course: "CS000",
    difficulty: "hard",
    styles: ["cloze"],
    instructions: "Keep answers under ten words.",
  });
  // Not the job tray: the registry drops a tracked id the server does not
  // know about, and only /decks/generate is routed here. The tray is covered
  // by "a generation started in the course hub survives leaving the tab".
  await expect(page.getByText(/flashcard deck queued/)).toBeVisible();
});

test("a deck needs at least one card type before it can be generated", async ({ page }) => {
  await page.goto("/notebook/course/CS000");
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await studio.getByRole("button", { name: /^flashcard deck/ }).click();

  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });
  for (const style of ["Definition", "Concept", "Cloze", "Application"]) {
    const box = dialog.getByRole("checkbox", { name: new RegExp(style) });
    if (await box.isChecked()) await box.uncheck();
  }
  await expect(dialog.getByText("Pick at least one card type.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "GENERATE" })).toBeDisabled();
});

test("exams finally send the difficulty and focus the backend has always accepted", async ({
  page,
}) => {
  // /api/study/exam has taken `difficulty` and `topics` since it was written,
  // and no UI ever sent either — so every exam silently generated at "medium"
  // with no focus. This is that gap closed.
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/study/exam", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-exam" }),
    });
  });

  await page.goto("/notebook/course/CS000");
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await studio.getByRole("button", { name: /^practice exam/ }).click();

  const dialog = page.getByRole("dialog", { name: "Generate a practice exam" });
  // No card types on an exam — those are a flashcard idea.
  await expect(dialog.getByRole("checkbox", { name: /Cloze/ })).toHaveCount(0);

  await dialog.getByRole("button", { name: "EASY" }).click();
  await dialog.getByLabel("Focus on (optional)").fill("dynamic programming");
  await dialog.getByRole("button", { name: "GENERATE" }).click();

  expect(sent).toMatchObject({
    course: "CS000",
    difficulty: "easy",
    topics: "dynamic programming",
    background: true,
  });
});

test("the deck library generates from the sources you pick, not the whole course", async ({
  page,
}) => {
  // The library used to open this dialog with a course dropdown and a line
  // promising to read the *entire* course, and no control anywhere to narrow
  // it. This is that gap closed.
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/flashcards/decks/generate", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-lib", deck_id: 998 }),
    });
  });

  // Built here rather than seeded: CS000 has no listable sources at startup
  // (course.md sits at the course root, outside the materials/notes zones),
  // and a startup seed would silently decide other tests' outcomes.
  await page.goto("/notebook/course/CS000");
  await ingestProbe(page, "e2e-deck-source");

  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: /GENERATE/ }).click();

  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });
  // Out here there is no SOURCES rail, so a course has to be named.
  await expect(dialog.getByRole("button", { name: "GENERATE" })).toBeDisabled();
  await dialog.getByLabel("Course").selectOption("CS000");

  await dialog.getByRole("button", { name: "PICK SOURCES" }).click();
  const row = dialog.getByRole("checkbox", { name: "Use e2e-deck-source as a source" });
  await expect(row).toBeVisible({ timeout: 15_000 });
  // Still refused with a course chosen and nothing ticked — "pick sources" has
  // to mean it, or it is the old dialog with extra steps.
  await expect(dialog.getByRole("button", { name: "GENERATE" })).toBeDisabled();

  await row.click();
  await dialog.getByRole("button", { name: "GENERATE" }).click();

  expect(sent).toMatchObject({
    course: "CS000",
    sources: ["15-Courses/CS000/materials/e2e-deck-source.md"],
  });
});

test("the whole course is still one click away", async ({ page }) => {
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/flashcards/decks/generate", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-whole", deck_id: 996 }),
    });
  });

  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: /GENERATE/ }).click();
  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });
  await dialog.getByLabel("Course").selectOption("CS000");
  await expect(dialog.getByText("reads everything indexed under the course")).toBeVisible();
  await dialog.getByRole("button", { name: "GENERATE" }).click();

  expect(sent).toMatchObject({ course: "CS000", sources: null });
});

test("a deck can be generated from a file the vault never sees", async ({ page }) => {
  let body: string | null = null;
  await page.route("**/api/flashcards/decks/generate/upload", async (route) => {
    body = route.request().postData();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-upload", deck_id: 997 }),
    });
  });

  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: /GENERATE/ }).click();
  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });

  await dialog.getByRole("button", { name: "MY OWN FILE" }).click();
  // No course needed on this route: a deck can be about a PDF rather than
  // about a course.
  await expect(dialog.getByRole("button", { name: "GENERATE" })).toBeDisabled();

  // `setInputFiles` drives the same handler a drop would, which is the whole
  // reason the zone is a label around a real input rather than a bare div.
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-upload-lecture.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(`# flow networks

Capacities bound the flow on an edge.
`),
  });
  await expect(dialog.getByText("▍e2e-upload-lecture.md")).toBeVisible();

  await dialog.getByRole("button", { name: "GENERATE" }).click();
  await expect(page.getByText(/flashcard deck queued/)).toBeVisible();
  expect(body).toContain("e2e-upload-lecture.md");
});

test("a file Argus cannot read is refused before it is uploaded", async ({ page }) => {
  await page.goto("/notebook/flashcards");
  await page.getByRole("button", { name: /GENERATE/ }).click();
  const dialog = page.getByRole("dialog", { name: "Generate a flashcard deck" });
  await dialog.getByRole("button", { name: "MY OWN FILE" }).click();

  await dialog.locator('input[type="file"]').setInputFiles({
    name: "cards.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`front,back
`),
  });

  // A round trip to be told "that is not a PDF" is a worse way to learn it.
  await expect(page.getByText(/isn't a kind Argus can read/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "GENERATE" })).toBeDisabled();
});

test("a deck can be renamed from the library, and the rename sticks", async ({
  page,
  request,
}) => {
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e rename me" } })
  ).json();

  await page.goto("/notebook/flashcards");
  const row = page.getByRole("listitem").filter({ hasText: "e2e rename me" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Rename e2e rename me" }).click();

  // Not scoped to `row`: entering edit mode swaps the title out for the field,
  // so a `hasText` filter on the old title stops matching its own row. Only one
  // row can be editing at a time, so the page-level field is unambiguous.
  const field = page.getByRole("textbox", { name: "Deck name" });
  await field.fill("e2e renamed deck");
  await field.press("Enter");

  const renamed = page.getByRole("listitem").filter({ hasText: "e2e renamed deck" });
  await expect(renamed).toBeVisible();

  // Survives a reload: the rename went to the database, not to local state.
  await page.reload();
  await expect(page.getByRole("listitem").filter({ hasText: "e2e renamed deck" })).toBeVisible();

  // And the deck's own page agrees, because its heading reads the same row.
  await page.goto(`/notebook/flashcards/${deck.id}`);
  await expect(page.getByRole("heading", { name: "e2e renamed deck" })).toBeVisible();
});

test("a courseless deck can be given the course EXPORT needs", async ({ page, request }) => {
  // EXPORT writes to the course's flashcards.md, so it is disabled without one
  // -- and its tooltip has said "set a course on this deck" since it shipped,
  // with nothing in the app able to do that.
  const deck = await (
    await request.post("/api/flashcards/decks", { data: { title: "e2e courseless deck" } })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: [{ front: "needs a course", back: "to be exported" }] },
  });

  await page.goto(`/notebook/flashcards/${deck.id}`);
  const exportButton = page.getByRole("button", { name: "EXPORT" });
  await expect(exportButton).toBeDisabled();

  await page.getByRole("button", { name: "Rename e2e courseless deck" }).click();
  await page.getByLabel("Deck course").fill("CS000");
  await page.getByRole("button", { name: "SAVE" }).click();

  await expect(exportButton).toBeEnabled();
});

test("a course's decks are one click away from the course", async ({ page, request }) => {
  const deck = await (
    await request.post("/api/flashcards/decks", {
      data: { title: "e2e hub deck", course: "CS000" },
    })
  ).json();
  await request.post(`/api/flashcards/decks/${deck.id}/cards`, {
    data: { cards: [{ front: "reachable?", back: "yes" }] },
  });

  await page.goto("/notebook/course/CS000");
  const panel = page.locator("section").filter({ hasText: "▍DECKS · CS000" });
  // Scoped to its own row, not to the panel: by now CS000 has several decks and
  // every one carries a `review N ->` link, so a panel-wide match finds them all.
  const item = panel.getByRole("listitem").filter({ hasText: "e2e hub deck" });
  const row = item.getByRole("link", { name: /e2e hub deck/ });
  await expect(row).toBeVisible();
  await expect(item.getByRole("link", { name: /review/i })).toBeVisible();

  // The old row pointed at /notebook/flashcards?deck=<id> -- a parameter nothing
  // in the app has ever read, so it landed on the library and left you to find
  // the deck by eye.
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/notebook/flashcards/${deck.id}$`));
});
