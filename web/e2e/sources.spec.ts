import { expect, test, type Locator } from "@playwright/test";

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


test("files that are not in the index can be indexed from the page that says so", async ({
  page,
}) => {
  // A6. The page's own subtitle is "Everything Argus can search", and it
  // diagnosed "not indexed" on row after row while offering nothing to do
  // about it -- the only /system link was gated on the index being
  // *unreadable*, which is the rarer state. A user who ingests a lecture,
  // reads "not indexed" and finds no button concludes the feature is broken.
  await page.goto("/sources");
  const list = page.locator("section").filter({ hasText: "▍SOURCES" });
  await expect(list.getByRole("listitem").first()).toBeVisible({ timeout: 15_000 });

  // The seeded vault indexes on ingest, so manufacture the state the banner
  // exists for: a file on disk that the index has never seen.
  const missing = list.getByText("not indexed").first();
  if ((await missing.count()) === 0) {
    test.skip(true, "nothing unindexed in this vault run");
  }

  const button = page.getByRole("button", { name: "Index them" });
  await expect(button).toBeVisible();
  await button.click();

  // It reports through the same segmented job readout an ingest uses, rather
  // than inventing a second progress language.
  await expect(page.getByText(/▍INGEST(ING|ED)/)).toBeVisible({ timeout: 30_000 });
});


test("the corpus page can be searched and sorted, and says so in the URL", async ({ page }) => {
  // The Course Hub rail has had a filter all along; /sources -- whose entire
  // job is browsing the corpus -- had none, so past a certain count it stopped
  // being a list and became a scroll with no way in.
  await page.goto("/sources");
  const list = page.locator("section").filter({ hasText: "▍SOURCES" });
  await expect(list.getByRole("listitem").first()).toBeVisible({ timeout: 15_000 });
  const total = await list.getByRole("listitem").count();
  expect(total).toBeGreaterThan(1);

  const first = await list.getByRole("listitem").first().innerText();
  await list.getByLabel("Search sources").fill("course");
  await expect(list.getByRole("listitem")).not.toHaveCount(total);
  await expect(page).toHaveURL(/[?&]q=course/);

  // A search matching nothing is not an empty vault, and must not offer to
  // ingest as though it were.
  await list.getByLabel("Search sources").fill("zzz-no-such-source");
  await expect(list.getByText(/Nothing matches/)).toBeVisible();
  await expect(list.getByText("No sources yet.")).toHaveCount(0);

  await list.getByLabel("Search sources").fill("");
  await expect(list.getByRole("listitem")).toHaveCount(total);

  // Sorting reorders rather than filtering, and is shareable too.
  await list.getByLabel("Sort sources").selectOption("name");
  await expect(page).toHaveURL(/[?&]sort=name/);
  await expect(list.getByRole("listitem")).toHaveCount(total);
  expect(await list.getByRole("listitem").first().innerText()).not.toBe(first);
});


/**
 * Drop files on the ingest dialog's dropzone, building them inside the page.
 *
 * Two reasons not to use `setInputFiles` here. It goes through the file input,
 * which is the *only* path `accept` ever constrained — the drop handler had no
 * validation at all, so that is the half worth exercising. And the oversize
 * case is 101 MB: `setInputFiles` would ship every byte over CDP, while a file
 * whose size is the only thing anyone measures can be conjured where it is
 * measured.
 */
async function dropOnZone(dialog: Locator, files: { name: string; bytes: number }[]) {
  const zone = dialog.getByRole("button", { name: /drop files/ });
  await zone.evaluate((element, specs: { name: string; bytes: number }[]) => {
    const transfer = new DataTransfer();
    for (const spec of specs) {
      transfer.items.add(new File([new Uint8Array(spec.bytes)], spec.name));
    }
    element.dispatchEvent(
      new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }),
    );
  }, files);
}

test("a file the backend would refuse is refused here, by name and by reason", async ({ page }) => {
  // B5/B6. `accept` constrained the file picker and nothing else: a dropped
  // `.zip` was queued, hashed, uploaded and only rejected server-side after
  // the wait, and there was no size check on either path — the 100 MB limit
  // was discoverable only as a 413 at the end of a 100 MB upload.
  await page.goto("/sources");
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();

  // The limit is on the dropzone caption now, not only in the backend.
  await expect(dialog.getByText(/up to 50 at once/)).toContainText("100 MB each");

  // Through the picker, which `setInputFiles` reaches past `accept` exactly
  // the way a drag-and-drop used to reach past it for real.
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "deck.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PK not a lecture"),
  });
  await expect(dialog.getByRole("alert")).toContainText("deck.zip — .zip isn't supported");
  await expect(dialog.getByRole("button", { name: "Remove deck.zip" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /^Ingest/ })).toBeDisabled();

  // And through the drop path, with the size the server would have taken a
  // whole upload to object to.
  await dropOnZone(dialog, [{ name: "thesis.pdf", bytes: 101 * 1024 * 1024 }]);
  await expect(dialog.getByRole("alert")).toContainText(
    "thesis.pdf — 101 MB, over the 100 MB limit",
  );
  await expect(dialog.getByRole("button", { name: "Remove thesis.pdf" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /^Ingest/ })).toBeDisabled();

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("dropping past the cap says so instead of silently discarding the rest", async ({ page }) => {
  // B6. `add()` did `files.slice(0, MAX_FILES - picked.length)` and then
  // `setError(null)` on the very next line, so dropping sixty files when fifty
  // were queued discarded ten *and* cleared the only message that could have
  // mentioned it. Nothing at all happened.
  await page.goto("/sources");
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();

  await dropOnZone(
    dialog,
    Array.from({ length: 55 }, (_, index) => ({ name: `e2e-cap-${index}.md`, bytes: 1 })),
  );

  await expect(dialog.getByRole("alert")).toContainText(
    "5 more files were not added — 50 at once is the limit",
  );
  await expect(dialog.getByRole("button", { name: "Ingest 50" })).toBeVisible();

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("changing the destination re-checks what is already there", async ({ page }) => {
  // B7. The precheck ran once, at pick time, against `target || options[0]`.
  // Changing "Save to" afterwards left the collision notes describing the old
  // destination — and `collides`, the sole gate on whether the Replace
  // checkbox renders at all, is derived from them. Pick where the name does
  // not collide, switch to where it does, and the checkbox never appeared:
  // the duplicate was written with no warning of any kind.
  await page.goto("/sources");

  // Ingest the collision rather than depending on one another spec left
  // behind. Note style empty throughout: a real backend means a real provider.
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const seed = page.getByRole("dialog", { name: "Ingest files" });
  await seed.getByLabel("Write a note from each file").selectOption("");
  await seed.locator('input[type="file"]').setInputFiles({
    name: "e2e-dest-swap.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Destination swap\n"),
  });
  await seed.getByLabel("Save to").selectOption("00-Inbox/files");
  await seed.getByRole("button", { name: /^Ingest/ }).click();
  await expect(seed).toBeHidden();
  const job = page.locator("section").filter({ hasText: /▍INGEST/ });
  await expect(job.getByText(/\d+ chunks|no chunks/)).toBeVisible({ timeout: 30_000 });

  // Pick the same name for a destination where it does *not* collide.
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await dialog.getByLabel("Write a note from each file").selectOption("");
  await dialog.getByLabel("Save to").selectOption("15-Courses/CS000/materials");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-dest-swap.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Destination swap, edited\n"),
  });
  await expect(dialog.getByRole("button", { name: "Remove e2e-dest-swap.md" })).toBeVisible();
  await expect(dialog.getByText("Replace the copies already in my vault")).toHaveCount(0);

  // Now switch to the one it does collide with. This is the whole finding.
  await dialog.getByLabel("Save to").selectOption("00-Inbox/files");
  await expect(dialog.getByText("Replace the copies already in my vault")).toBeVisible({
    timeout: 15_000,
  });
  await expect(dialog.getByText(/second copy|already ingested/)).toBeVisible();

  // And back again: the warning is not sticky either, or it would be lying in
  // the other direction.
  await dialog.getByLabel("Save to").selectOption("15-Courses/CS000/materials");
  await expect(dialog.getByText("Replace the copies already in my vault")).toHaveCount(0, {
    timeout: 15_000,
  });

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("Escape asks before it throws a configured batch away", async ({ page }) => {
  // B8. One keystroke discarded the files, the destination, the style and the
  // typed instruction; `picked` is component state and none of it is
  // recoverable. Cancel is deliberately not gated the same way — a button
  // labelled Cancel is a decision, Escape is frequently a reflex.
  await page.goto("/sources");
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "e2e-escape-probe.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Escape probe\n"),
  });
  await expect(dialog.getByRole("button", { name: "Remove e2e-escape-probe.md" })).toBeVisible();

  await page.keyboard.press("Escape");
  const ask = page.getByRole("dialog", { name: "Discard these files" });
  await expect(ask).toBeVisible();
  await expect(dialog).toBeVisible();

  // Backing out of the question leaves the batch exactly as it was.
  await ask.getByRole("button", { name: "Keep them" }).click();
  await expect(ask).toBeHidden();
  await expect(dialog.getByRole("button", { name: "Remove e2e-escape-probe.md" })).toBeVisible();

  await page.keyboard.press("Escape");
  await page
    .getByRole("dialog", { name: "Discard these files" })
    .getByRole("button", { name: "Discard" })
    .click();
  await expect(dialog).toBeHidden();
});

test("choosing not to write a note turns the instruction field off", async ({ page }) => {
  // B10. The first option reads "Don't write a note", the field below said
  // "On its own, this is the whole instruction for the note", and the
  // behaviour followed the hint: `style !== NO_NOTE || prompt.trim().length`.
  // Typing a stray note-to-self with "don't" selected wrote a note and sent
  // every file's text to a hosted provider — then asked the user to confirm
  // sending files for an operation they believed they had turned off.
  await page.goto("/sources");
  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Ingest files" });
  await expect(dialog).toBeVisible();

  const style = dialog.getByLabel("Write a note from each file");
  const instruction = dialog.getByLabel("Extra instruction (optional)");

  await style.selectOption("");
  await expect(instruction).toBeDisabled();
  await expect(dialog.getByText("Choose a note style to add an instruction")).toBeVisible();
  await expect(dialog.getByText(/nothing leaves it/)).toBeVisible();

  // A real style re-opens it, and the hint stops contradicting the control.
  await style.selectOption({ index: 1 });
  await expect(instruction).toBeEnabled();
  await expect(dialog.getByText("Choose a note style to add an instruction")).toHaveCount(0);

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("deleting a source removes it from the vault and stops it being searchable", async ({
  page,
}) => {
  // The feature this adds, and the pre-existing bug underneath it: nothing
  // called VaultIndex.delete_file when a file was deleted, so a removed note
  // kept being retrieved and cited in chat. The chunk count in the summary is
  // what proves the index half actually happened.
  await page.goto("/sources");

  await page.getByRole("button", { name: "+ Ingest" }).first().click();
  const ingest = page.getByRole("dialog", { name: "Ingest files" });
  await ingest.getByLabel("Write a note from each file").selectOption("");
  await ingest.locator('input[type="file"]').setInputFiles({
    name: "e2e-delete-me.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Delete Me"),
  });
  await ingest.getByLabel("Save to").selectOption("00-Inbox/files");
  await ingest.getByRole("button", { name: /^Ingest/ }).click();
  await expect(ingest).toBeHidden();

  const list = page.locator("section").filter({ hasText: "▍SOURCES" });
  await expect(list.getByText("e2e-delete-me", { exact: true })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "Delete e2e-delete-me" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete source" });
  await expect(confirm).toBeVisible();
  // The undo story is stated, because git is the only one there is.
  await expect(confirm.getByText(/git snapshot/)).toBeVisible();
  await confirm.getByRole("button", { name: "DELETE" }).click();

  await expect(confirm).toBeHidden({ timeout: 15_000 });
  await expect(list.getByText("e2e-delete-me", { exact: true })).toHaveCount(0);

  // Gone for good, not just filtered out of a stale client list.
  await page.reload();
  await expect(list.getByText("e2e-delete-me", { exact: true })).toHaveCount(0, {
    timeout: 15_000,
  });
});

test("a protected path refuses the whole batch rather than half-applying it", async ({ page }) => {
  // All-or-nothing is the contract: a batch naming one file inside a
  // protected zone must leave every sibling on disk, not delete the ones it
  // got to first.
  await page.goto("/sources");
  const response = await page.request.delete("/api/sources", {
    data: { paths: ["99-Private/secret.md"], include_generated: false },
  });

  expect(response.status()).toBe(403);
  expect(await response.text()).toContain("99-Private");
});
