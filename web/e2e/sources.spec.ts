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
  const job = page.locator("section").filter({ hasText: "▍INGESTING" });
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
  const job = page.locator("section").filter({ hasText: "▍INGESTING" });
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
