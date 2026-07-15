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
  await expect(page.getByText("TOKENS.CLAUDE")).toBeVisible(); // preview panel
  await expect(page.getByText("ACTIVITY.FEED")).toBeVisible();
  await expect(page.getByText("INSIGHTS.14D")).toBeVisible();
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
  page.on("dialog", (dialog) => dialog.accept());
  const row = page.locator("li", { hasText: "E2E delete me" });
  await row.hover();
  await row.getByRole("button", { name: "Delete task" }).click();

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
