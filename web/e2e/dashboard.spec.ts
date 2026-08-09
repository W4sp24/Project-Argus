import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const vault = path.join(__dirname, ".workdir", "vault");

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

test("dashboard renders all widgets", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByText("due today")).toBeVisible(); // stat tile
  await expect(page.getByText("ACTIVITY.HEATMAP")).toBeVisible();
  await expect(page.getByTestId("heatmap")).toBeVisible();
  await expect(page.getByText("PLANNER.TIMELINE")).toBeVisible();
  await expect(page.getByText("TASKS.DUE")).toBeVisible();
  await expect(page.getByText("INGEST")).toBeVisible();
  await expect(page.getByRole("button", { name: "Chat", exact: true })).toBeVisible(); // drawer toggle (chat left the inline rail in Phase F)
  await expect(page.getByText("ARGUS.AGENT")).toBeVisible(); // restyled briefing card
  await expect(page.getByText("ARGUS.USAGE")).toBeVisible(); // preview panel
  await expect(page.getByText("ACTIVITY.FEED")).toBeVisible();
  await expect(page.getByText("INSIGHTS.14D")).toBeVisible();
});

/**
 * The two cards the n8n rewiring is for. Everything below is a regression that
 * shipped: the quick-add had no submit control at all, undated tasks were
 * fetched and rendered nowhere, n8n events had no duration, and neither card
 * could write anything back.
 */

/**
 * A dashboard Panel by its eyebrow label.
 *
 * `.last()` is load-bearing: `filter({ hasText })` matches every *ancestor*
 * `<section>` too, and the seeded `calendar` widget renders the same events a
 * second time in the AutomationWidgets grid — which arrives asynchronously.
 * An outer match therefore flips from one "Team sync" row to two mid-assertion.
 * Ancestors precede descendants in DOM order, so `.last()` is the panel itself.
 */
function panel(page: import("@playwright/test").Page, label: string) {
  return page.locator("section").filter({ hasText: `▍${label}` }).last();
}

test("quick-add has a real submit button, not a decorative +", async ({ page }) => {
  await page.goto("/dashboard");
  const add = page.getByRole("button", { name: "Add task" });
  await expect(add).toBeVisible();
  // Disabled on an empty field, enabled once there's text — i.e. it is wired
  // to the form rather than being a span that looks like a button.
  await expect(add).toBeDisabled();
  await page.getByLabel("Add a task").fill("something to do");
  await expect(add).toBeEnabled();
});

test("TASKS.DUE says which store the quick-add writes to", async ({ page }) => {
  await page.goto("/dashboard");
  // seed_automations installs "Argus: Add Todoist Task", so the action
  // resolves and the destination is Todoist rather than a vault capture.
  await expect(page.getByText("→ todoist, via Argus: Add Todoist Task")).toBeVisible();
});

test("TASKS.DUE renders a NO DUE DATE group", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByText("NO DUE DATE")).toBeVisible();
});

test("PLANNER.TIMELINE navigates between days", async ({ page }) => {
  await page.goto("/dashboard");
  const timeline = panel(page, "PLANNER.TIMELINE");
  await expect(timeline.getByText("TODAY", { exact: true })).toBeVisible();
  await timeline.getByRole("button", { name: "Next day" }).click();
  await expect(timeline.getByText("TOMORROW", { exact: true })).toBeVisible();
  await timeline.getByRole("button", { name: "Previous day" }).click();
  await expect(timeline.getByText("TODAY", { exact: true })).toBeVisible();
});

test("PLANNER.TIMELINE offers + EVENT once the action is installed", async ({ page }) => {
  await page.goto("/dashboard");
  const timeline = panel(page, "PLANNER.TIMELINE");
  await timeline.getByRole("button", { name: "Add calendar event" }).click();
  await expect(timeline.getByLabel("Title")).toBeVisible();
  await expect(timeline.getByText("via Argus: Add Calendar Event")).toBeVisible();
});

test("an n8n-sourced event shows a real duration and its location", async ({ page }) => {
  await page.goto("/dashboard");
  const timeline = panel(page, "PLANNER.TIMELINE");
  const row = timeline.locator("li", { hasText: "Team sync" });
  // Seeded one hour long. Before `end` survived validation every n8n event was
  // zero-length and this label was empty.
  await expect(row.getByText("1h", { exact: true })).toBeVisible();
  await expect(row.getByText("@ Conf room A")).toBeVisible();
});

test("heatmap counts the seeded completion", async ({ page }) => {
  await page.goto("/dashboard");
  const cell = page.locator(`[data-testid="heatmap"] rect[data-date="${localToday()}"]`);
  await expect(cell).toHaveCount(1);
  // Seeded: one ✅ today (tasks) — count must be at least 1 on the "all" metric.
  const count = Number(await cell.getAttribute("data-count"));
  expect(count).toBeGreaterThanOrEqual(1);
});

test("check-off writes ✅ to the vault after a git snapshot", async ({ page }) => {
  await page.goto("/dashboard");
  const row = page.locator("li", { hasText: "E2E check me off" });
  await row.getByRole("button", { name: "Mark done" }).click();

  const file = path.join(vault, "20-Projects", "e2e.md");
  await expect
    .poll(() => fs.readFileSync(file, "utf-8"))
    .toContain(`- [x] E2E check me off 📅 ${localToday()} ✅ ${localToday()}`);

  const gitLog = execSync("git log --oneline", { cwd: vault, encoding: "utf-8" });
  expect(gitLog).toContain("argus: pre-apply snapshot (toggle task 20-Projects/e2e.md");
});

test("task delete removes the line, snapshot first", async ({ page }) => {
  // "Move the meeting" is due 2026-07-20 — a fixed fixture date the
  // roundtrip suggestion targets verbatim — which falls outside the
  // overdue/today agenda bucket relative to the real clock and so never
  // renders on the dashboard. Use the dedicated always-due-today seed line
  // instead so the row is actually visible to delete.
  await page.goto("/dashboard");
  const row = page.locator("li", { hasText: "E2E delete me" });
  await row.hover();
  await row.getByRole("button", { name: "Delete task" }).click();

  // Confirmation is an in-app dialog now, not window.confirm — the old
  // `page.on("dialog", …)` handler has nothing left to accept.
  const confirmDialog = page.getByRole("dialog", { name: "Delete task" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "Delete" }).click();

  const file = path.join(vault, "20-Projects", "e2e.md");
  await expect.poll(() => fs.readFileSync(file, "utf-8")).not.toContain("E2E delete me");
  const gitLog = execSync("git log --oneline", { cwd: vault, encoding: "utf-8" });
  expect(gitLog).toContain("argus: pre-apply snapshot (delete task 20-Projects/e2e.md");
});

test("chat thread persists between drawer and chat tab", async ({ page }) => {
  await page.goto("/dashboard");
  // No live agent in e2e: the ws will error, but the user message must survive
  // in shared state across surfaces (provider-level persistence).
  await page.getByRole("button", { name: "Chat", exact: true }).click(); // TopBar toggle opens the drawer
  await page.getByPlaceholder("Ask your vault").fill("hello from the dock");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("link", { name: "Open fullscreen chat" }).click(); // drawer ⛶ → /chat
  await expect(page).toHaveURL(/\/chat/);
  await expect(page.getByText("hello from the dock")).toBeVisible();
});

test("command palette opens on ctrl+K and closes on Escape", async ({ page }) => {
  await page.goto("/dashboard");
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await expect(palette).toBeVisible();
  await expect(palette.getByText("generate briefing")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(palette).toBeHidden();
});

test("note modal opens from + NOTE and closes on Escape", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "+ NOTE" }).click();
  const modal = page.getByRole("dialog", { name: "Add note" });
  await expect(modal).toBeVisible();
  await expect(modal.getByLabel("Note title")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(modal).toBeHidden();
});
