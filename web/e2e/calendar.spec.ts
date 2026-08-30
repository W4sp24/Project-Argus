import { expect, test, type Page } from "@playwright/test";

/**
 * /calendar e2e (see web/app/(dashboard)/calendar/page.tsx).
 *
 * The property worth an e2e at all is the one no unit test reaches: **the
 * calendar works with nothing connected.** pytest proves the API returns
 * events; only a browser proves the page renders them, that a recurring row
 * expands into several visible occurrences, and that an event from a
 * subscribed feed is presented as read-only rather than offering an edit the
 * API refuses.
 *
 * Fixtures come from e2e/seed_calendar.py, written straight through the store:
 * subscribing through the UI would need a keyring (CI installs no backend for
 * one) and a live .ics endpoint. Those paths are covered by pytest with a fake
 * keyring and httpx.MockTransport.
 *
 * All specs share ONE backend/DB (playwright.config.ts runs a single throwaway
 * vault, workers: 1), so state persists between tests. Anything created here
 * uses a unique title and assertions are scoped to it — never to a total count.
 */

const LOCAL_TITLE = "Seeded dentist visit";
const RECURRING_TITLE = "Seeded weekly standup";
const SUBSCRIBED_TITLE = "Seeded lecture from feed";

let uniqueCounter = 0;

function uniq(prefix: string): string {
  uniqueCounter += 1;
  return `${prefix} ${Date.now()}-${uniqueCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

/** The day rail — the list of the selected day's events.
 *
 * Filtered on "DAY ·" rather than "DAY": the bare word appears inside other
 * panels' prose, and `filter({hasText})` matches ancestors too, so `.last()`
 * is what keeps this on the rail itself rather than a section containing it.
 */
function dayRail(page: Page) {
  return page.locator("section").filter({ hasText: "DAY ·" }).last();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/calendar");
  // The grid is server-rendered empty and filled by SWR, so wait for real
  // data rather than for the route — otherwise every assertion below races
  // the first fetch.
  await expect(page.getByText(LOCAL_TITLE).first()).toBeVisible({ timeout: 15_000 });
});

test("a fresh install shows its own events with nothing connected", async ({ page }) => {
  // The whole point of the feature: no OAuth client, no n8n, no token.
  await expect(page.getByText(LOCAL_TITLE).first()).toBeVisible();
});

test("a weekly series expands into several occurrences in the month", async ({ page }) => {
  // Expansion happens on read, so this is the assertion that catches a
  // window predicate which shows a series once and never again.
  const occurrences = page.getByText(RECURRING_TITLE);
  expect(await occurrences.count()).toBeGreaterThan(1);
});

test("an event from a subscribed feed is shown as read-only", async ({ page }) => {
  await expect(page.getByText(SUBSCRIBED_TITLE).first()).toBeVisible();
  // The marker matters more than its wording: an .ics feed cannot be written
  // back, and offering an edit that 422s is worse than not offering one.
  await expect(page.getByText(/read.only/i).first()).toBeVisible();
});

test("an event created in the browser appears on the day it was given", async ({ page }) => {
  const title = uniq("E2E appointment");

  // Scoped to the rail: an unscoped /EVENT/i would also match the month
  // grid's own chips, since accessible-name matching is a substring.
  await dayRail(page).getByRole("button", { name: /EVENT/i }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  await dialog.getByLabel(/title/i).fill(title);
  // "CREATE" for a new event; an existing one submits with "SAVE".
  await dialog.getByRole("button", { name: /^create$/i }).click();

  await expect(dialog).toBeHidden();
  await expect(page.getByText(title).first()).toBeVisible();

  // And it survives a reload — i.e. it was persisted, not just rendered
  // optimistically. An optimistic row that never reached SQLite looks
  // identical until the page is refreshed.
  await page.reload();
  await expect(page.getByText(title).first()).toBeVisible({ timeout: 15_000 });
});

test("the day rail lists the selected day's events", async ({ page }) => {
  await expect(dayRail(page).getByText(LOCAL_TITLE).first()).toBeVisible();
});
