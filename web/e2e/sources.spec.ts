import { expect, test } from "@playwright/test";

/**
 * These run against the real backend, so `generator` is a real provider call.
 * Every ingest here therefore leaves the summary instruction empty — the
 * save/index/progress path is what this route added, and exercising the
 * summary would make the suite depend on a live model.
 */

test("sources lists the vault with its chunk counts", async ({ page }) => {
  await page.goto("/sources");

  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();
  await expect(page.getByText("▍SOURCES")).toBeVisible();
  await expect(page.getByText("▍FOLDERS")).toBeVisible();

  // The seeded vault has real notes, so the list must not be the empty state.
  await expect(page.getByRole("button", { name: "All folders" })).toBeVisible();
  await expect(page.getByText("No sources yet.")).toHaveCount(0);
});

test("ingesting a file reports every stage and leaves it in the list", async ({ page }) => {
  await page.goto("/sources");

  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();

  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-lecture.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# E2E Lecture\n\nA fact only this file knows.\n"),
  });
  await expect(dialog.getByText("e2e-lecture.md")).toBeVisible();

  await dialog.getByLabel("Save to").selectOption("00-Inbox/files");
  await dialog.getByRole("button", { name: /^Ingest/ }).click();

  // The dialog closes and the job panel takes over — this is the black-box fix:
  // the file is named, and its outcome is reported, not just "uploading…".
  // Scoped to the job panel: the source rows below carry chunk counts too.
  await expect(dialog).toBeHidden();
  // Matches the whole family: the header is driven by job.status now, so it
  // reads INGESTING while it runs and INGESTED once it settles.
  const job = page.locator("section").filter({ hasText: /▍INGEST/ });
  await expect(job).toBeVisible();
  await expect(job.getByText("e2e-lecture.md")).toBeVisible();
  await expect(job.getByText(/\d+ chunks|no chunks/)).toBeVisible({ timeout: 30_000 });
  await expect(job.getByText("done", { exact: true })).toBeVisible({ timeout: 30_000 });

  // …and it is a source afterwards, still there across a reload. The listed
  // title is the stem: the file has no frontmatter title to prefer.
  await page.reload();
  await expect(page.getByText("e2e-lecture", { exact: true })).toBeVisible({ timeout: 15_000 });
});

test("re-ingesting the same name warns before it writes a second copy", async ({ page }) => {
  await page.goto("/sources");

  const upload = async (body: string) => {
    await page.getByRole("button", { name: "+ Ingest" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Ingest files" });
    await dialog.locator('input[type="file"]').setInputFiles({
      name: "e2e-duplicate.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(body),
    });
    await dialog.getByLabel("Save to").selectOption("00-Inbox/files");
    return dialog;
  };

  const first = await upload("# First\n");
  await first.getByRole("button", { name: /^Ingest/ }).click();
  await expect(first).toBeHidden();
  // Matches the whole family: the header is driven by job.status now, so it
  // reads INGESTING while it runs and INGESTED once it settles.
  const job = page.locator("section").filter({ hasText: /▍INGEST/ });
  await expect(job.getByText(/\d+ chunks|no chunks/)).toBeVisible({ timeout: 30_000 });

  // Second time round the precheck has something to find, so the collision is
  // shown *before* the upload rather than discovered as a mystery `-2` file.
  const second = await upload("# Second, corrected\n");
  await expect(second.getByText(/second copy|already ingested/)).toBeVisible({ timeout: 15_000 });
  await expect(second.getByText("Replace the copies already in my vault")).toBeVisible();
  await second.getByRole("button", { name: "Cancel" }).click();
  await expect(second).toBeHidden();
});

test("the dashboard ingest panel links through to sources", async ({ page }) => {
  await page.goto("/dashboard");

  await page.getByRole("link", { name: "sources →" }).click();

  await expect(page).toHaveURL(/\/sources$/);
  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();
});

/**
 * The invariant the UX audit asked for by name: the destination the dialog
 * *displays* is the destination it *POSTs*. The two used to diverge silently —
 * `initialTarget` is whatever folder the rail was filtered to, it was never
 * checked against the registered destinations, and a <select> with no matching
 * <option> renders index 0 while React state keeps the unlisted value. Files
 * landed in the course root: outside every zone, so invisible to the Course
 * Hub's listing and to the notes-gap list, while the UI reported success.
 */
test("the destination the dialog shows is the destination it writes to", async ({ page }) => {
  await page.goto("/sources");

  // A course root is the case that broke: it is a real vault folder and it is
  // not one of the registered ingest destinations.
  await page.getByRole("button", { name: /^15-Courses\/CS000/ }).first().click();

  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();

  // It must have snapped to a legal destination inside that course rather than
  // holding a value with no matching option — materials, not notes, because
  // that is where a lecture belongs and what the hub counts.
  const select = dialog.getByLabel("Save to");
  await expect(select).toHaveValue("15-Courses/CS000/materials");

  // Intercepted rather than allowed through: the invariant under test is
  // about the *request*, and actually writing here would drop a file into
  // the shared CS000 fixture that later specs read as their starting state.
  let posted: string | null = null;
  await page.route("**/api/ingest/jobs", async (route) => {
    const body = route.request().postData() ?? "";
    posted = /name="target"\r?\n\r?\n([^\r\n]*)/.exec(body)?.[1] ?? "";
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "e2e-intercepted" }),
    });
  });

  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-target-probe.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Target Probe\n"),
  });
  const displayed = await select.inputValue();
  await dialog.getByRole("button", { name: /^Ingest/ }).click();

  await expect.poll(() => posted, { timeout: 15_000 }).not.toBeNull();
  expect(posted).toBe(displayed);
  expect(posted).toBe("15-Courses/CS000/materials");
});


test("the job panel stops claiming it is ingesting once it has finished", async ({ page }) => {
  // It used to read "▍INGESTING" over a job whose own status line said "done",
  // and there was no way to clear it: `jobId` was only ever set, so the panel
  // sat there until navigation and a second ingest silently replaced the first
  // job's report. The completion summary is the receipt, so it is dismissed
  // deliberately rather than auto-hidden.
  await page.goto("/sources");

  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-dismiss-probe.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Dismiss Probe"),
  });
  await dialog.getByLabel("Save to").selectOption("00-Inbox/files");
  await dialog.getByRole("button", { name: /^Ingest/ }).click();
  await expect(dialog).toBeHidden();

  const job = page.locator("section").filter({ hasText: /▍INGEST/ });
  await expect(job.getByText("done", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("▍INGESTED", { exact: true })).toBeVisible();
  await expect(page.getByText("▍INGESTING", { exact: true })).toHaveCount(0);

  await job.getByRole("button", { name: "dismiss" }).click();
  await expect(page.getByText(/▍INGEST(ED|ING)/)).toHaveCount(0);
});


test("the folder rail keeps its siblings, agrees with its counts, and is a deep link", async ({
  page,
}) => {
  // Three defects in one control. The rail was derived from the *filtered*
  // response, so clicking a folder deleted every sibling from the navigation
  // that got you there -- the only way out was "All folders". Its counts were
  // computed by exact `source.folder` while the API filtered by subtree, so a
  // folder with children could never agree with itself (the audit saw 3 vs
  // 18). And the view was component state, so it could not be linked to and
  // Back left the page instead of undoing the click.
  await page.goto("/sources");
  const rail = page.locator("section").filter({ hasText: "▍FOLDERS" });
  const list = page.locator("section").filter({ hasText: "▍SOURCES" });
  // "All folders" is static markup, so waiting on it says nothing about the
  // fetch. Wait for real rows before counting anything.
  await expect(list.getByRole("listitem").first()).toBeVisible({ timeout: 15_000 });

  const siblingsBefore = await rail.getByRole("button").count();
  expect(siblingsBefore).toBeGreaterThan(2);

  const target = rail.getByRole("button", { name: /^15-Courses\/CS000, / });
  const label = (await target.getAttribute("aria-label")) ?? "";
  const claimed = Number(/, (\d+) file/.exec(label)?.[1]);
  expect(claimed).toBeGreaterThan(0);
  await target.click();

  // The rail is navigation, not results: it does not shrink when used.
  await expect(rail.getByRole("button")).toHaveCount(siblingsBefore);

  // The count on the button is what clicking it actually shows.
  await expect(list.getByRole("listitem")).toHaveCount(claimed);

  // The view is in the URL, and Back undoes the click rather than leaving.
  await expect(page).toHaveURL(/[?&]folder=15-Courses%2FCS000/);
  await page.goBack();
  await expect(page).toHaveURL(/\/sources$/);
});
