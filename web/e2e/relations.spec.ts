import { expect, test } from "@playwright/test";

/**
 * One spec, deliberately.
 *
 * The job store allows one index-group job at a time and 409s otherwise, and
 * a relink now contends for that slot alongside ingest and reindex. A spec
 * that fired several of them back to back would lose the second and third —
 * and it surfaces as a *missing row*, not an error, so it reads like a
 * selection bug and costs an afternoon. See the 2026-08-29 session notes.
 *
 * Relink rather than ingest, also deliberately. These run against a real
 * backend, so asking it to generate a note would make the suite depend on a
 * live model; `ingest.spec.ts` leaves every summary instruction empty for
 * that reason. Relinking calls no generator, so it is the half of note
 * relationships a black-box test can actually reach.
 *
 * Fixtures come from e2e/seed_relations.py: one generated note in its
 * pre-feature shape, and one hand-written note that must survive untouched.
 */

test("relinking connects a generated note and leaves a hand-written one alone", async ({
  page,
}) => {
  await page.goto("/sources");
  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();

  await page.getByRole("button", { name: /Relink notes/i }).click();

  // The trigger answers a job id, and the readout is the same segmented
  // progress component an ingest uses.
  const job = page.locator("section").filter({ hasText: /▍(RELINK|INGEST)/ });
  await expect(job).toBeVisible({ timeout: 30_000 });
  await expect(job.getByText("wk1-graphs.notes.md")).toBeVisible({ timeout: 60_000 });
  await expect(job.getByText("done", { exact: true })).toBeVisible({ timeout: 60_000 });

  // The note now carries relationships. Read through the API rather than the
  // UI: the vault file is the artifact this feature produces, and /sources
  // renders a listing, not a note body.
  const noted = await page.request.get(
    "/api/note?path=" +
      encodeURIComponent("15-Courses/CS000/notes/wk1-graphs.notes.md"),
  );
  expect(noted.ok()).toBeTruthy();
  const content = (await noted.json()).content as string;

  expect(content).toContain("<!-- argus:relations:start -->");
  expect(content).toContain("## Related");
  // The bug this feature fixes: the source link used to be `[[wk1-graphs]]`,
  // naming no path at all.
  expect(content).toContain("[[15-Courses/CS000/materials/wk1-graphs|wk1-graphs]]");
  expect(content).toContain("[[15-Courses/CS000/course|CS000]]");
  expect(content).toContain("argus/note");
  expect(content).toContain("course/CS000");

  // The guard, end to end: a note without `generated_by: argus` is untouched.
  const mine = await page.request.get(
    "/api/note?path=" + encodeURIComponent("15-Courses/CS000/notes/my-own-notes.md"),
  );
  expect(mine.ok()).toBeTruthy();
  const untouched = (await mine.json()).content as string;
  expect(untouched).toBe("---\ntitle: my own notes\n---\n\nHand written. Do not touch.\n");
});
