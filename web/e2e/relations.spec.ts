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
 * Fixtures are created *inside the test*, not seeded at startup. The suite
 * runs `workers: 1` against one shared vault, so a startup seed is global
 * state: the first draft of this spec seeded a material into CS000 and broke
 * `study.spec.ts:128`, which asserts GUIDE is disabled precisely because that
 * course has none. They also live under `50-Reference/` rather than in a
 * course, so nothing here can change what the Course Hub reports.
 */

const SOURCE = "50-Reference/e2e-graphs.md";
const NOTE = "50-Reference/e2e-graphs.summary.md";
const MINE = "50-Reference/e2e-my-own-notes.md";

/** The note shape Argus wrote before `backend/vault/relations.py` existed:
 *  no tags, no topics, no related, and a trailing wikilink with the source's
 *  extension stripped — the bug this feature fixes. Verbatim, so the spec is
 *  a regression test rather than a smoke test. */
const PRE_FEATURE_NOTE = `---
generated_by: argus
note_style: summary
prompt: ''
source: ${SOURCE}
title: e2e-graphs — summary
type: note
---

Graphs are vertices and edges; BFS explores them level by level.

## Key points

- A graph is a set of vertices and the edges between them.

[[e2e-graphs]]
`;

const HAND_WRITTEN = "---\ntitle: my own notes\n---\n\nHand written. Do not touch.\n";

test("relinking connects a generated note and spares a hand-written one", async ({ page }) => {
  const create = async (path: string, content: string) => {
    const response = await page.request.post("/api/note/create", {
      data: { path, content },
    });
    expect(response.ok(), `could not create ${path}`).toBeTruthy();
  };

  await create(SOURCE, "# Graphs\n\nA graph is a set of vertices and edges.\n");
  await create(NOTE, PRE_FEATURE_NOTE);
  await create(MINE, HAND_WRITTEN);

  await page.goto("/sources");
  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();

  await page.getByRole("button", { name: /Relink notes/i }).click();

  const job = page.locator("section").filter({ hasText: /▍(RELINK|INGEST)/ });
  await expect(job).toBeVisible({ timeout: 30_000 });
  await expect(job.getByText("done", { exact: true }).first()).toBeVisible({
    timeout: 90_000,
  });

  // Read the vault file rather than the UI: the note is the artifact this
  // feature produces, and /sources renders a listing, not a note body.
  const noted = await page.request.get("/api/note?path=" + encodeURIComponent(NOTE));
  expect(noted.ok()).toBeTruthy();
  const content = (await noted.json()).content as string;

  expect(content).toContain("<!-- argus:relations:start -->");
  expect(content).toContain("## Related");
  // The bug: the source link used to be `[[e2e-graphs]]`, naming no path.
  expect(content).toContain("[[50-Reference/e2e-graphs|e2e-graphs]]");
  expect(content).toContain("argus/note");

  // The guard, end to end: no `generated_by: argus`, so it is untouched.
  const mine = await page.request.get("/api/note?path=" + encodeURIComponent(MINE));
  expect(mine.ok()).toBeTruthy();
  expect((await mine.json()).content).toBe(HAND_WRITTEN);
});
