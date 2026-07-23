import { expect, test, type Page } from "@playwright/test";

/**
 * QUICK.LINKS dashboard panel e2e (see web/components/dashboard/QuickLinks.tsx).
 *
 * All tests in this file share ONE backend/DB instance (playwright.config.ts
 * spins up a single throwaway-vault backend + next dev), so state persists
 * between tests. Every test therefore uses its own unique label(s) and scopes
 * assertions to those labels rather than asserting on the panel's total row
 * count.
 */

let uniqueCounter = 0;

/** A label guaranteed not to collide with (or substring-match) any other
 * label generated in this run. */
function uniq(prefix: string): string {
  uniqueCounter += 1;
  return `${prefix} ${Date.now()}-${uniqueCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

/** The QUICK.LINKS panel's <section> — scopes every locator below so we never
 * accidentally match rows/forms belonging to another dashboard widget. */
function quickLinksPanel(page: Page) {
  return page.locator("section").filter({ hasText: "QUICK.LINKS" });
}

/** The always-present add-link form, scoped by its ADD submit button.
 *
 * The `has:` locator must be built from `page` (a bare root locator), not
 * from an already-filtered locator like `panel`/`row` — Playwright applies a
 * "has" locator's full selector chain *relative to each candidate element*,
 * so a `panel.getByRole(...)` filter bakes in panel's own ancestor selector
 * and gets re-applied inside the candidate's subtree (where it can never
 * match), silently narrowing to zero elements and hanging until timeout. */
function addForm(page: Page) {
  return page.locator("form").filter({ has: page.getByRole("button", { name: "ADD" }) });
}

/** Add a link and WAIT until its row has been persisted + rendered. The submit
 * is async (POST + SWR refetch) and the ADD button is `disabled` while it's in
 * flight, so a caller that fires a second add immediately would have its click
 * dropped. Waiting for the row here serializes back-to-back adds and de-flakes
 * every valid-add test. (The rejection test does its own fill+click since it
 * expects NO row.) */
async function addLink(page: Page, label: string, url: string) {
  const form = addForm(page);
  await form.getByLabel("Link label").fill(label);
  await form.getByLabel("Link URL").fill(url);
  await form.getByRole("button", { name: "ADD" }).click();
  await expect(quickLinksPanel(page).locator("li").filter({ hasText: label })).toBeVisible({
    timeout: 15_000,
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByText("▍QUICK.LINKS")).toBeVisible();
});

test("add normalizes a bare host to an https URL", async ({ page }) => {
  const label = uniq("E2E Docs");
  await addLink(page, label, "example.com");

  const panel = quickLinksPanel(page);
  const row = panel.locator("li").filter({ hasText: label });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: label })).toHaveAttribute("title", "https://example.com");
});

test("rejecting a dangerous URL shows a toast and adds no row", async ({ page }) => {
  const label = uniq("E2E Danger");
  // Inline fill+click (not addLink) because a rejected submit produces NO row —
  // addLink would wait forever for one.
  const form = addForm(page);
  await form.getByLabel("Link label").fill(label);
  await form.getByLabel("Link URL").fill("javascript:alert(1)");
  await form.getByRole("button", { name: "ADD" }).click();

  await expect(page.getByText("quick-links :: enter a label and a valid https URL")).toBeVisible();

  const panel = quickLinksPanel(page);
  await expect(panel.locator("li").filter({ hasText: label })).toHaveCount(0);
});

test("editing a link updates its label in place", async ({ page }) => {
  const label = uniq("E2E Edit Me");
  const newLabel = uniq("E2E Edited");
  await addLink(page, label, "https://example.org");

  const panel = quickLinksPanel(page);
  const row = panel.locator("li").filter({ hasText: label });
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByRole("button", { name: "Edit link" }).click();

  // Once editing, the label lives in an <input value> rather than as text, so
  // the `hasText: label` row filter above no longer matches this <li>. The
  // edited row is the ONLY <li> that contains a <form> (the add form is a
  // direct child of the panel, not inside any <li>), so target it directly.
  const editForm = panel.locator("li form");
  await editForm.getByLabel("Link label").fill(newLabel);
  await editForm.getByRole("button", { name: "SAVE" }).click();

  await expect(panel.locator("li").filter({ hasText: newLabel })).toBeVisible();
  await expect(panel.locator("li").filter({ hasText: label })).toHaveCount(0);
});

test("reordering moves a link past its neighbor", async ({ page }) => {
  const labelA = uniq("E2E Reorder A");
  const labelB = uniq("E2E Reorder B");
  await addLink(page, labelA, "https://a.example.com");
  await addLink(page, labelB, "https://b.example.com");

  const panel = quickLinksPanel(page);
  const rowA = panel.locator("li").filter({ hasText: labelA });
  await expect(rowA).toBeVisible();
  // Confirm starting order is A, B before acting, so the post-move
  // assertion actually proves a swap happened.
  await expect(async () => {
    const texts = (await panel.locator("li").allTextContents()).filter(
      (t) => t.includes(labelA) || t.includes(labelB),
    );
    expect(texts).toEqual([expect.stringContaining(labelA), expect.stringContaining(labelB)]);
  }).toPass();

  await rowA.hover();
  await rowA.getByRole("button", { name: "Move down" }).click();

  await expect(async () => {
    const texts = (await panel.locator("li").allTextContents()).filter(
      (t) => t.includes(labelA) || t.includes(labelB),
    );
    expect(texts).toEqual([expect.stringContaining(labelB), expect.stringContaining(labelA)]);
  }).toPass();
});

test("clicking a link opens its sanitized URL via the window.open fallback", async ({ page }) => {
  // No window.argus in the e2e browser, so openExternalUrl falls back to
  // window.open — stub it before the app's scripts run so we can capture
  // the url it was called with.
  await page.addInitScript(() => {
    (window as unknown as { open: (u: string) => Window | null }).open = (u: string) => {
      (window as unknown as { __opened?: string }).__opened = u;
      return null;
    };
  });
  await page.goto("/dashboard");
  await expect(page.getByText("▍QUICK.LINKS")).toBeVisible();

  const label = uniq("E2E Navigate");
  await addLink(page, label, "https://playwright.dev");

  const panel = quickLinksPanel(page);
  const row = panel.locator("li").filter({ hasText: label });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: label }).click();

  // The client-side sanitizer (web/lib/quickLinks.ts) round-trips the url
  // through `new URL(...).toString()`, which normalizes an empty path to
  // "/" — so the captured url gains a trailing slash the raw input didn't
  // have. This matches sanitizeUrl("https://playwright.dev") exactly.
  await expect.poll(() => page.evaluate(() => (window as unknown as { __opened?: string }).__opened)).toBe(
    "https://playwright.dev/",
  );
});
