import { expect, test } from "@playwright/test";

/**
 * Chat e2e.
 *
 * There is no live agent here — `dashboard.spec.ts` says so out loud, and the
 * ws turn always ends in an error frame. So nothing in this file waits for an
 * answer. Everything that needs a rendered *answer* reads a transcript
 * `e2e/seed_chat_threads.py` wrote straight into the store, and everything
 * that needs a real round trip leans on the one thing the backend does before
 * it ever calls a model: `_open_turn` creates the thread and persists the user
 * message, then the failure arrives as a frame on a socket that stays open.
 */

const SEEDED = "Where I stand in CS000";

test("a seeded thread restores its markdown, tool trace and citations", async ({ page }) => {
  await page.goto("/chat");

  const rail = page.getByRole("navigation", { name: "Conversations" });
  await rail.getByText(SEEDED).click();

  // Markdown, not the literal characters. A table proves remark-gfm is on:
  // without it these rows render as a paragraph full of pipes.
  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("cell", { name: "Graphs" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "CS000 — where you stand" })).toBeVisible();

  // The fenced block is highlighted, so the keyword is its own element rather
  // than a run of plain text inside <pre>.
  await expect(page.locator("pre code .hljs-keyword").first()).toBeVisible();

  // The trace persisted as tools_json folds back into the same two steps the
  // live stream would have shown.
  const trace = page.getByRole("button", { name: /2 steps/ });
  await expect(trace).toBeVisible();
  await expect(trace).toHaveAttribute("aria-expanded", "false");
  await trace.click();
  await expect(page.getByText("Search vault")).toBeVisible();
  await expect(page.getByText("Read note")).toBeVisible();

  // Citations come off the trace's paths, so the chip is a real obsidian link.
  // It addresses the note by absolute path: `vault=` is matched against the
  // vault's *registered* name in Obsidian, which drifts from the folder name
  // and produced the reported "Vault not found" dialog.
  const chip = page.getByRole("link", { name: /course\.md/ }).first();
  await expect(chip).toHaveAttribute("href", /^obsidian:\/\/open\?path=/);
  await expect(chip).toHaveAttribute("href", /15-Courses.*course\.md$/);

  // The inline [path.md] marker the prompt asks the model to emit is stripped
  // before rendering, so it does not double up with the chip above.
  await expect(page.getByText("[15-Courses/CS000/course.md]")).toHaveCount(0);

  // Maths is typeset, not shown as source. Asserting on `.katex-mathml math`
  // rather than just `.katex` on purpose: that node only exists under
  // `output: "htmlAndMathml"`, so a regression to `output: "html"` — which
  // still looks right and is silently unreadable to a screen reader — fails
  // here instead of shipping.
  const transcript = page.getByRole("log");
  await expect(transcript.locator(".katex-mathml math").first()).toBeAttached();
  await expect(transcript.locator(".katex-display")).toHaveCount(1);
  // The delimiters themselves must be gone, and no expression may have failed
  // to parse.
  await expect(page.getByText("$$")).toHaveCount(0);
  await expect(transcript.locator(".katex-error")).toHaveCount(0);

  // ...and stripping it leaves the rest of the answer's whitespace alone.
  // `stripCitationMarkers` used to collapse every run of two-plus spaces in
  // the whole message to clean up after itself, which flattened indentation
  // it had nothing to do with: this sub-list rendered as three siblings.
  await expect(page.locator("li", { hasText: "Graphs" }).locator("ul li")).toHaveCount(2);
});

test("the rail buckets threads by day and badges a course thread", async ({ page }) => {
  await page.goto("/chat");
  const rail = page.getByRole("navigation", { name: "Conversations" });

  // Seeded one per bucket, at fixed offsets from now — so these are stable
  // regardless of when CI runs.
  await expect(rail.getByText("TODAY")).toBeVisible();
  await expect(rail.getByText("YESTERDAY")).toBeVisible();
  await expect(rail.getByText("EARLIER")).toBeVisible();
  await expect(rail.getByText("Reading list for the week")).toBeVisible();
  await expect(rail.getByText("Thesis outline")).toBeVisible();

  // A course-scoped thread stays in the unscoped list, told apart by a badge
  // rather than hidden — store.list_threads only filters when asked.
  await expect(rail.getByText("CS000 exam prep")).toBeVisible();
});

test("a first message creates a thread that can be renamed and deleted", async ({ page }) => {
  await page.goto("/chat");
  const rail = page.getByRole("navigation", { name: "Conversations" });

  await rail.getByRole("button", { name: "New thread" }).click();
  await page.getByPlaceholder("Ask your vault").fill("E2E thread lifecycle");
  await page.getByRole("button", { name: "Send" }).click();

  // The row exists because _open_turn wrote it before the model was called,
  // and its title was derived from the message. The turn itself never lands.
  //
  // Every control below is scoped to its own row rather than reached with
  // `.first()`. The rail is full of other threads by now, and a stray
  // `.first()` would act on whichever one happened to sort to the top.
  const row = rail.getByRole("listitem").filter({ hasText: "E2E thread lifecycle" });
  await expect(row).toBeVisible();

  await row.hover();
  await row.getByRole("button", { name: "Rename thread" }).click();
  // Not scoped to `row`: entering edit mode swaps the row's title out for the
  // field, so a `hasText` filter on the old title stops matching its own row.
  // Only one row can be editing at a time, so the rail-level field is
  // unambiguous.
  const field = rail.getByRole("textbox", { name: "Thread title" });
  await field.fill("E2E renamed");
  await field.press("Enter");

  const renamed = rail.getByRole("listitem").filter({ hasText: "E2E renamed" });
  await expect(renamed).toBeVisible();

  // Survives a reload: the rename went to the database, not to local state.
  await page.reload();
  await expect(renamed).toBeVisible();

  await renamed.hover();
  await renamed.getByRole("button", { name: "Delete thread" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Delete thread" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "Delete" }).click();
  await expect(confirmDialog).toBeHidden();
  await expect(renamed).toHaveCount(0);
});

test("a turn in flight can be stopped, and the question survives", async ({ page }) => {
  // STOP only means something while a turn is genuinely in flight, and against
  // the real backend that state has no guaranteed duration: with no agent
  // configured the turn ends by itself, sometimes as an error frame and
  // sometimes as a dropped socket. Whichever landed first decided which
  // assertion below broke — :132 saw STOP patched into a disabled SEND (React
  // reuses the node, so a *missing* button presents as a disabled one), while
  // :140 saw `stop()` early-return on a cleared `busyRef` and leave the message
  // in "error" rather than "stopped". Mocking the socket and never answering is
  // what makes this a test about STOP instead of a race against how fast the
  // backend gives up. No connectToServer(), so Playwright opens the socket in
  // the page and nothing ever reaches the real one.
  await page.routeWebSocket("**/ws/chat", () => {});

  await page.goto("/chat");
  await page.getByRole("button", { name: "New thread" }).click();
  await page.getByPlaceholder("Ask your vault").fill("does this survive");
  await page.getByRole("button", { name: "Send" }).click();

  // The composer stays usable throughout rather than greying the whole surface
  // out. Scoped to the transcript: the thread title is derived from the
  // message, so the same words also appear in the rail and the page header.
  const transcript = page.getByRole("log", { name: "Conversation" });
  const stop = page.getByRole("button", { name: "Stop generating" });
  await expect(stop).toBeVisible();
  await expect(transcript.getByText("does this survive")).toBeVisible();

  await stop.click();

  // Stopping drops the socket. The client returns to a sendable state instead
  // of staying stuck busy, and the question is still on screen — the old
  // client dropped the pending bubble on a socket error and took the answer
  // with it.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(transcript.getByText("does this survive")).toBeVisible();
  await expect(transcript.getByText("■ stopped")).toBeVisible();
});
