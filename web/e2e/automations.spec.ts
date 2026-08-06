import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * F8: e2e coverage for the n8n automations feature — /automations, the
 * dashboard widgets, and the AUTOMATIONS.HUD panel.
 *
 * State comes entirely from web/e2e/seed_automations.py (run once by
 * start-backend.mjs before the shared throwaway-vault backend starts): two
 * registered instances, widgets covering all four states, two cached
 * "action" workflows with finished runs, and a spread of activity events.
 * web/e2e/stub-n8n.mjs is wired in as a third playwright.config.ts
 * webServer, but nothing here calls it — see the note below.
 *
 * Deliberately NOT covered, and why:
 *   - Registering an n8n instance through the connect dialog, issuing/
 *     rotating an external token, or firing a form/button trigger from the
 *     command palette. The CI `e2e` job installs no `keyrings.alt`, so any
 *     of those (they all write a credential, or need one already resolved)
 *     would fail there even though they pass locally. That is why the
 *     instances here are seeded straight into `.argus/automations.json`
 *     instead of driven through the UI, and why stub-n8n.mjs — even though
 *     it is wired into the harness and does work — is not exercised by any
 *     spec in this file. This suite tests rendering, state and layout only.
 *   - Drag/resize/reorder of dashboard widgets (AutomationWidgets.tsx) and
 *     the WidgetInspectDialog raw-payload view. Both are plausible follow-up
 *     coverage, not part of the F8 brief.
 */

function panel(page: Page, label: string) {
  return page.locator("section").filter({ hasText: label });
}

/** InstanceGrid renders plain `<li>`s (no Panel wrapper) — scoped by the
 * always-present "+ ADD INSTANCE" card, whose accessible name is
 * "ADD INSTANCE" ("+" is `aria-hidden`). */
function instanceGrid(page: Page) {
  return page.locator("ul").filter({ has: page.getByRole("button", { name: "ADD INSTANCE" }) });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/automations");
});

test("renders the instance cards, the tab switcher, and the instance filter", async ({ page }) => {
  const grid = instanceGrid(page);
  await expect(grid.getByText("alpha-n8n")).toBeVisible();
  await expect(grid.getByText("bravo-n8n")).toBeVisible();
  await expect(grid.getByText("http://127.0.0.1:5679")).toBeVisible();
  await expect(grid.getByText("http://127.0.0.1:5680")).toBeVisible();

  await expect(page.getByRole("button", { name: "ACTIVE" })).toBeVisible();
  await expect(page.getByRole("button", { name: "GALLERY" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ACTIVITY" })).toBeVisible();

  // The instance filter only renders once there is more than one instance to
  // tell apart — both seeded instances qualify, and ACTIVE (the default tab)
  // is one of the two tabs it renders beside.
  await expect(page.getByRole("button", { name: "ALL", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "alpha-n8n", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "bravo-n8n", exact: true })).toBeVisible();
});

test("the ACTIVE tab lists both displays and actions in one list", async ({ page }) => {
  const registered = panel(page, "REGISTERED");
  // 5 seeded widgets + 2 seeded cached workflows.
  await expect(registered.getByText("REGISTERED · 7")).toBeVisible();

  // Displays — including the same "calendar" slug pushed by both instances,
  // rendered as two distinct rows under the composite (instance_id, slug) key.
  await expect(registered.locator("li").filter({ hasText: "Upcoming events" })).toHaveCount(2);
  await expect(registered.locator("li").filter({ hasText: "Active tasks" })).toHaveCount(1);
  await expect(registered.locator("li").filter({ hasText: "Inbox zero" })).toHaveCount(1);
  await expect(registered.locator("li").filter({ hasText: "Current temperature" })).toHaveCount(1);

  // Actions.
  await expect(registered.locator("li").filter({ hasText: "E2E Echo (seeded)" })).toHaveCount(1);
  await expect(registered.locator("li").filter({ hasText: "E2E Button (seeded)" })).toHaveCount(1);

  // One unified list — DISPLAY and ACTION kind chips both present, not two
  // separate panels.
  await expect(registered.getByText("DISPLAY").first()).toBeVisible();
  await expect(registered.getByText("ACTION").first()).toBeVisible();

  // State badges for the non-LIVE widgets.
  await expect(
    registered.locator("li").filter({ hasText: "Active tasks" }).getByText(/STALE/),
  ).toBeVisible();
  await expect(
    registered.locator("li").filter({ hasText: "Current temperature" }).getByText("WAITING"),
  ).toBeVisible();

  // Action health, derived from each seeded workflow's own last run.
  await expect(
    registered.locator("li").filter({ hasText: "E2E Echo (seeded)" }).getByText("READY"),
  ).toBeVisible();
  await expect(
    registered.locator("li").filter({ hasText: "E2E Button (seeded)" }).getByText("FAILING"),
  ).toBeVisible();
});

test("the ACTIVITY tab shows seeded events and the tag filters narrow them", async ({ page }) => {
  await page.getByRole("button", { name: "ACTIVITY" }).click();
  const activity = panel(page, "ACTIVITY");

  await expect(activity.getByText("ACTIVITY · 6")).toBeVisible();
  await expect(activity.getByText("completed in 2.0s")).toBeVisible();
  await expect(activity.getByText("completed in 0.8s")).toBeVisible();
  await expect(activity.getByText("n8n returned 500")).toBeVisible();
  await expect(activity.getByText("installed as workflow wf-installed-1")).toBeVisible();
  await expect(activity.getByText("pushed 2 tasks")).toBeVisible();
  await expect(activity.getByText("pushed 2 calendar entries")).toBeVisible();

  // Narrow to RUN — exactly the 2 seeded RUN events, everything else drops out.
  await activity.getByRole("button", { name: "run", exact: true }).click();
  await expect(activity.getByText("ACTIVITY · 2")).toBeVisible();
  await expect(activity.getByText("completed in 2.0s")).toBeVisible();
  await expect(activity.getByText("completed in 0.8s")).toBeVisible();
  await expect(activity.getByText("n8n returned 500")).toHaveCount(0);
  await expect(activity.getByText("installed as workflow wf-installed-1")).toHaveCount(0);

  // Narrow to FAIL — the one seeded FAIL event.
  await activity.getByRole("button", { name: "fail", exact: true }).click();
  await expect(activity.getByText("ACTIVITY · 1")).toBeVisible();
  await expect(activity.getByText("n8n returned 500")).toBeVisible();
  await expect(activity.getByText("completed in 2.0s")).toHaveCount(0);
});

test("the GALLERY tab lists bundled templates with their derived chips", async ({ page }) => {
  await page.getByRole("button", { name: "GALLERY" }).click();
  const gallery = panel(page, "GALLERY");

  // Names come straight from each bundled workflow JSON's own "name" field —
  // this is the tab that shipped as a placeholder once, so it must render
  // real, definition-derived content, not just "a tab with something in it".
  await expect(gallery.getByText("Argus: Google Calendar → Timeline Widget")).toBeVisible();
  await expect(gallery.getByText("Argus: Todoist → Task List Widget")).toBeVisible();
  await expect(gallery.getByText("Argus: Weather → Metric Widget")).toBeVisible();
  await expect(gallery.getByText("Argus: Mobile Capture")).toBeVisible();
  await expect(gallery.getByText("Argus: Add Calendar Event")).toBeVisible();

  // exact: true throughout -- the card's own description prose also contains
  // "timeline"/"metric" as ordinary words (e.g. "...as a timeline widget"),
  // so a substring match would hit the description as well as the chip.
  const calendarCard = gallery.locator("li").filter({ hasText: "Argus: Google Calendar" });
  await expect(calendarCard.getByText("timeline", { exact: true })).toBeVisible();
  await expect(calendarCard.getByText("every 15m", { exact: true })).toBeVisible();

  const weatherCard = gallery.locator("li").filter({ hasText: "Argus: Weather" });
  await expect(weatherCard.getByText("metric", { exact: true })).toBeVisible();
  await expect(weatherCard.getByText("every 30m", { exact: true })).toBeVisible();

  const captureCard = gallery.locator("li").filter({ hasText: "Argus: Mobile Capture" });
  await expect(captureCard.getByText("2 fields", { exact: true })).toBeVisible();

  await expect(gallery.getByRole("button", { name: "INSTALL", exact: true }).first()).toBeVisible();
});

test("the dashboard renders the automation widgets and each of the four states is visually distinguishable", async ({
  page,
}) => {
  await page.goto("/dashboard");

  // LIVE — fresh, within its declared cadence: real data, no state badge.
  const live = panel(page, "Upcoming events").filter({ hasText: "alpha-n8n" });
  await expect(live).toBeVisible();
  await expect(live.getByText("Team sync")).toBeVisible();
  await expect(live.getByText(/STALE|WAITING/)).toHaveCount(0);

  // STALE — past 2.5x its cadence, but the last good push is still shown
  // (dimmed and labelled), never hidden.
  const stale = panel(page, "Active tasks");
  await expect(stale).toBeVisible();
  await expect(stale.getByText(/STALE/)).toBeVisible();
  await expect(stale.getByText("Reply to registrar")).toBeVisible();

  // EMPTY — a fresh push, genuinely zero items: reassurance copy, no badge
  // (a badge on a healthy panel would be noise, per WidgetShell's own doc).
  const empty = panel(page, "Inbox zero");
  await expect(empty).toBeVisible();
  await expect(empty.getByText("Nothing here.")).toBeVisible();
  await expect(empty.getByText("reported empty")).toBeVisible();
  await expect(empty.getByText(/STALE|WAITING/)).toHaveCount(0);

  // WAITING — installed, never pushed.
  const waiting = panel(page, "Current temperature");
  await expect(waiting).toBeVisible();
  await expect(waiting.getByText("WAITING")).toBeVisible();
  await expect(waiting.getByText("No data received yet.")).toBeVisible();
});

test("the origin chip appears on dashboard widgets when two instances are registered", async ({ page }) => {
  await page.goto("/dashboard");

  // The "calendar" slug is pushed by both instances — same title
  // ("Upcoming events") on both, distinguishable only by origin.
  const calendarA = panel(page, "Upcoming events").filter({ hasText: "alpha-n8n" });
  const calendarB = panel(page, "Upcoming events").filter({ hasText: "bravo-n8n" });
  await expect(calendarA).toBeVisible();
  await expect(calendarB).toBeVisible();
});

test("the AUTOMATIONS.HUD panel renders", async ({ page }) => {
  await page.goto("/dashboard");
  const hud = panel(page, "AUTOMATIONS.HUD");

  await expect(hud).toBeVisible();
  await expect(hud.getByRole("button", { name: "MANAGE →" })).toBeVisible();
  await expect(hud.getByRole("button", { name: "RUN AN ACTION" })).toBeVisible();

  // The most recent seeded event — a RUN at `now`, not the FAIL event from 5
  // minutes earlier — proving the ambient readout really orders by time and
  // not, say, severity.
  await expect(hud.getByText(/E2E Echo \(seeded\)/)).toBeVisible();
  await expect(hud.getByText(/completed in 0\.8s/)).toBeVisible();
});

test.describe("design review screenshots", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("captures full-page automations + dashboard screenshots", async ({ page }) => {
    const dir = path.join(__dirname, ".screenshots");
    fs.mkdirSync(dir, { recursive: true });

    await page.goto("/automations");
    await expect(page.getByRole("button", { name: "ACTIVE" })).toBeVisible();
    await expect(panel(page, "REGISTERED")).toBeVisible();
    await page.screenshot({ path: path.join(dir, "automations-active.png"), fullPage: true });

    await page.getByRole("button", { name: "GALLERY" }).click();
    await expect(page.getByText("Argus: Google Calendar → Timeline Widget")).toBeVisible();
    await page.screenshot({ path: path.join(dir, "automations-gallery.png"), fullPage: true });

    await page.getByRole("button", { name: "ACTIVITY" }).click();
    await expect(page.getByText("ACTIVITY · 6")).toBeVisible();
    await page.screenshot({ path: path.join(dir, "automations-activity.png"), fullPage: true });

    await page.goto("/dashboard");
    await expect(page.getByText("AUTOMATIONS.HUD")).toBeVisible();
    await page.screenshot({ path: path.join(dir, "dashboard.png"), fullPage: true });
  });
});

test("a widget can be resized, and the new span survives a reload", async ({ page }) => {
  // Resize shipped with two affordances and no coverage: a pointer corner
  // handle and this keyboard-reachable cycle button. The button is the one
  // that can be driven headlessly, and it exercises the same applyPatch path
  // the drag handle does — including the PATCH that marks the widget as
  // user-controlled. Without this, "resizable" was only ever an assertion
  // about the source.
  await page.goto("/dashboard");

  // Scope to ONE widget by its own accessible name. Two seeded widgets share
  // the title "Upcoming events" (same slug, two instances — which is the
  // point), so a positional locator is ambiguous by construction.
  const button = () => page.getByRole("button", { name: /Resize Active tasks/ });
  await expect(button()).toBeVisible();

  const before = (await button().textContent())?.trim();

  // Wait for the WRITE, not the label. The label moves optimistically the
  // instant it is clicked, so reloading on that signal can abort the PATCH
  // still in flight and the assertion then fails for a reason that has
  // nothing to do with whether resize persists.
  const saved = page.waitForResponse(
    (r) => r.url().includes("/api/automations/widgets/tasks") && r.request().method() === "PATCH",
  );
  await button().click();
  expect((await saved).status()).toBe(200);

  const afterClick = (await button().textContent())?.trim();
  expect(afterClick).not.toBe(before);

  await page.reload();
  await expect(button()).toBeVisible();
  await expect
    .poll(async () => (await button().textContent())?.trim(), { timeout: 10_000 })
    .toBe(afterClick);

  // The pointer affordance exists alongside it — neither is the only way in.
  expect(await page.locator("[class*='cursor-nwse-resize']").count()).toBeGreaterThan(0);
});

test("with no automations at all, the dashboard still points at the feature", async ({ page }) => {
  // The zero state is where most people sit: a widget can only appear after an
  // n8n instance is registered AND the inbound surface is switched on, so
  // rendering nothing meant the dashboard never hinted the feature existed.
  // The seeded vault has widgets, so empty is simulated at the network edge
  // rather than by destroying shared fixture state other specs depend on.
  await page.route("**/api/automations/widgets*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.goto("/dashboard");

  const hint = page.getByText("automations · no live panels yet");
  await expect(hint).toBeVisible();
  const setUp = page.getByRole("link", { name: /set one up/i });
  await expect(setUp).toBeVisible();
  await expect(setUp).toHaveAttribute("href", "/automations");

  // Dismissible, and it stays dismissed — a permanent hint for someone who
  // will never run n8n is the noise the old render-nothing rule guarded against.
  await page.getByRole("button", { name: "Hide the automations hint" }).click();
  await expect(hint).toBeHidden();
  await page.reload();
  await expect(page.getByText("automations · no live panels yet")).toBeHidden();

  // Dismissing the hint must not touch the way in that always exists.
  // role="tab", set explicitly, so this is not a "button".
  await expect(page.getByRole("tab", { name: /AUTO/ })).toBeVisible();
});

test("the connect dialog says what it is waiting for instead of a dead button", async ({ page }) => {
  // A probe persists nothing, so it needs only a URL and a key. Gating it on
  // the instance name too made TEST CONNECTION disabled while both fields
  // that matter were filled — a button that does nothing and never says why,
  // which is indistinguishable from a broken one.
  await page.goto("/automations");
  await page.getByRole("button", { name: /ADD INSTANCE/i }).click();

  const primary = page.getByRole("button", { name: /TEST CONNECTION/i });
  await expect(primary).toBeDisabled();
  await expect(page.getByText("enter the n8n base url")).toBeVisible();

  await page.getByLabel(/n8n base url/i).fill("localhost:5678");
  await expect(page.getByText(/must start with http/i)).toBeVisible();

  await page.getByLabel(/n8n base url/i).fill("http://127.0.0.1:5678");
  await expect(page.getByText("paste an n8n api key")).toBeVisible();

  // URL + key alone is enough to test. No instance name required.
  await page.getByLabel(/api key/i).fill("some-key");
  await expect(primary).toBeEnabled();
});

test("a failed connection test says so on the step the button is on", async ({ page }) => {
  // The regression this guards: a not-ok probe left `probed` false, so the
  // button label stayed "TEST CONNECTION" and nothing else on step 0
  // changed. The request had run and been refused, but the dialog looked
  // simply dead. Feedback has to land where the button is.
  await page.route("**/api/automations/instance/test", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        detail: "connection refused",
        latency_ms: null,
        workflow_count: null,
      }),
    }),
  );
  await page.goto("/automations");
  await page.getByRole("button", { name: /ADD INSTANCE/i }).click();
  await page.getByLabel(/n8n base url/i).fill("http://127.0.0.1:5678");
  await page.getByLabel(/api key/i).fill("some-key");
  await page.getByRole("button", { name: /TEST CONNECTION/i }).click();

  await expect(page.getByText("✕ could not connect")).toBeVisible();
  await expect(page.getByText("connection refused")).toBeVisible();
});

test("a passing connection test reports the tagged workflow count", async ({ page }) => {
  await page.route("**/api/automations/instance/test", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, detail: "ok", latency_ms: 12, workflow_count: 3 }),
    }),
  );
  await page.goto("/automations");
  await page.getByRole("button", { name: /ADD INSTANCE/i }).click();
  await page.getByLabel(/n8n base url/i).fill("http://127.0.0.1:5678");
  await page.getByLabel(/api key/i).fill("some-key");
  await page.getByRole("button", { name: /TEST CONNECTION/i }).click();

  // The count is the proof the key works and that discovery will find
  // something — a bare tick says neither.
  await expect(page.getByText("✓ connected")).toBeVisible();
  await expect(page.getByText(/3 workflows tagged argus/)).toBeVisible();
});

test("a rejected key blames the key, not the url", async ({ page }) => {
  // The URL is *proven* good when n8n answers 401 — it answered. Suggesting
  // a different URL there sends the user to debug the one part that works.
  await page.route("**/api/automations/instance/test", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        reason: "auth",
        detail: "n8n rejected the API key — check it and try again",
        latency_ms: 9,
        workflow_count: null,
      }),
    }),
  );
  await page.goto("/automations");
  await page.getByRole("button", { name: /ADD INSTANCE/i }).click();
  await page.getByLabel(/n8n base url/i).fill("http://localhost:5678");
  await page.getByLabel(/api key/i).fill("stale-key");
  await page.getByRole("button", { name: /TEST CONNECTION/i }).click();

  await expect(page.getByText("✕ reached n8n, key rejected")).toBeVisible();
  await expect(page.getByText(/The URL is fine — n8n answered/)).toBeVisible();
  // And it must NOT tell them to change the URL.
  await expect(page.getByText(/try 127\.0\.0\.1/)).toHaveCount(0);
});
