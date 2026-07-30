import { expect, test } from "@playwright/test";

// Regression test for the reported bug: "Research — deleting seems to not
// work." LIBRARY.QUEUE and HIGHLIGHTS.RECENT used to be local React state
// seeded from a hardcoded mock array — `×` looked like it deleted a row, but
// nothing was ever written to the vault, so the row came back on the next
// reload (and there was never a real paper to delete in the first place).
// The reload after each mutation below is the entire point of this file.

test("adding a paper persists across reload, and deleting it removes it permanently, even after a reload", async ({
  page,
}) => {
  await page.goto("/research");

  await page.getByLabel("Paper title").fill("E2E Delete Me Paper");
  await page.getByLabel("Authors and venue").fill("E2E et al. · Test Conf 2026");
  await page.getByRole("button", { name: "+ ADD PAPER" }).click();
  await expect(page.getByText("E2E Delete Me Paper")).toBeVisible();
  await expect(page.getByText("E2E et al. · Test Conf 2026")).toBeVisible();

  await page.reload();
  await expect(page.getByText("E2E Delete Me Paper")).toBeVisible();

  await page.getByRole("button", { name: "Delete E2E Delete Me Paper" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Delete E2E Delete Me Paper" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "DELETE" }).click();
  await expect(page.getByText("E2E Delete Me Paper")).not.toBeVisible();

  await page.reload();
  await expect(page.getByText("E2E Delete Me Paper")).not.toBeVisible();
});

test("cycling a paper's status persists across reload", async ({ page }) => {
  await page.goto("/research");

  await page.getByLabel("Paper title").fill("E2E Status Cycle Paper");
  await page.getByRole("button", { name: "+ ADD PAPER" }).click();
  const status = page.getByRole("button", { name: "Cycle status, currently QUEUED" });
  await expect(status).toBeVisible();

  await status.click();
  await expect(page.getByRole("button", { name: "Cycle status, currently READING" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: "Cycle status, currently READING" })).toBeVisible();

  // Clean up so this test is repeatable against the shared e2e vault.
  await page.getByRole("button", { name: "Delete E2E Status Cycle Paper" }).click();
  await page
    .getByRole("dialog", { name: "Delete E2E Status Cycle Paper" })
    .getByRole("button", { name: "DELETE" })
    .click();
});

test("adding a highlight persists across reload, and deleting it removes it permanently, even after a reload", async ({
  page,
}) => {
  await page.goto("/research");

  const highlightInput = page.getByLabel("Add a highlight");
  await highlightInput.fill("E2E highlight — self-attention is O(1) sequential ops.");
  await highlightInput.press("Enter");
  await expect(page.getByText("E2E highlight — self-attention is O(1) sequential ops.")).toBeVisible();

  await page.reload();
  await expect(page.getByText("E2E highlight — self-attention is O(1) sequential ops.")).toBeVisible();

  const row = page.locator("li", { hasText: "E2E highlight — self-attention is O(1) sequential ops." });
  await row.hover();
  await row.getByRole("button", { name: "Delete highlight" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Delete highlight" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "DELETE" }).click();
  await expect(page.getByText("E2E highlight — self-attention is O(1) sequential ops.")).not.toBeVisible();

  await page.reload();
  await expect(page.getByText("E2E highlight — self-attention is O(1) sequential ops.")).not.toBeVisible();
});
