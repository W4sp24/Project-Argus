import { expect, test } from "@playwright/test";

/**
 * Phase H specs: the /system DOCTOR panel renders real `POST /api/doctor`
 * checks, and EMAIL.CAPTURE really posts to `POST /api/ingest/email` —
 * verified end-to-end by the toast plus a proposal landing in the review
 * queue (`GET /api/review`).
 */

test("/system doctor renders real checks", async ({ page }) => {
  await page.goto("/system");

  // Real check names from backend/doctor.py run_checks(), not mocks.
  await expect(page.getByText("vault-git").first()).toBeVisible();
  await expect(page.getByText("database", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("keyring", { exact: true }).first()).toBeVisible();

  // The throwaway e2e vault is git-initialized by `argus init`, so at least
  // the vault checks report OK.
  await expect(page.getByText("OK", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "RUN AGAIN" })).toBeVisible();
});

test("email capture posts and lands a proposal in the review queue", async ({ page }) => {
  const before = await page.request.get("/api/review");
  const beforeCount = ((await before.json()) as unknown[]).length;

  await page.goto("/dashboard");
  await page
    .getByPlaceholder("paste an email…")
    .fill(
      "Subject: E2E email capture\n\nHi,\n\n- [ ] reply to the registrar by 2026-07-20\n\nThanks,\nRegistrar <registrar@example.edu>",
    );
  await page.getByRole("button", { name: "EXTRACT →" }).click();

  // The backend archives the email, runs extraction (agent with a
  // deterministic fallback — either path ends in a suggestion row), and the
  // toast points at the review queue. Generous timeout: the live agent can
  // be slow on this machine.
  await expect(page.getByText(/email archived → 00-Inbox\/emails\//)).toBeVisible({
    timeout: 45_000,
  });

  const after = await page.request.get("/api/review");
  const suggestions = (await after.json()) as { rationale: string }[];
  expect(suggestions.length).toBeGreaterThanOrEqual(beforeCount + 1);
  expect(suggestions.some((s) => s.rationale.startsWith("email capture:"))).toBe(true);
});
