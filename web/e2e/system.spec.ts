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
  // Ollama is one way to run Argus, so it is reported but never fatal.
  await expect(page.getByText("ollama", { exact: true }).first()).toBeVisible();

  // The throwaway e2e vault is git-initialized by `argus init`, so at least
  // the vault checks report OK.
  await expect(page.getByText("OK", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "RUN AGAIN" })).toBeVisible();
});

test("local model browser scores real catalog models against this machine", async ({ page }) => {
  await page.goto("/system");

  // Real entries from backend/agent/model_catalog.py, scored by the real
  // hardware probe — no mock data.
  await expect(page.getByText("Llama 3.2 1B")).toBeVisible();
  await expect(page.getByText(/this pc ::/)).toBeVisible();

  // Every row carries a fit verdict badge.
  const verdicts = page.getByText(/^(FITS|SLOW|TOO BIG|UNKNOWN)$/);
  expect(await verdicts.count()).toBeGreaterThan(0);

  // Ollama's storage location is surfaced read-only.
  await expect(page.getByText(/Downloads go through Ollama at/)).toBeVisible();
});

test("adding a model requires passing the connection test first", async ({ page }) => {
  await page.goto("/system");
  await page.getByRole("button", { name: "+ ADD MODEL" }).click();

  const dialog = page.getByRole("dialog", { name: "Add a model" });
  await expect(dialog).toBeVisible();

  // Ollama is the default choice, so the endpoint is prefilled — a
  // non-developer never has to know this URL.
  await expect(dialog.getByLabel("Display name")).toBeVisible();
  await expect(page.getByPlaceholder("http://127.0.0.1:11434/v1")).toHaveValue(
    "http://127.0.0.1:11434/v1",
  );

  // Naming it is not enough: Save stays locked until the test goes green.
  await dialog.getByLabel("Display name").fill("e2e-model");
  await expect(dialog.getByRole("button", { name: "SAVE MODEL" })).toBeDisabled();

  // Nothing is running on 11434 in CI, so the test must fail *readably*
  // rather than hang or save a broken entry.
  await dialog.getByRole("button", { name: "TEST CONNECTION" }).click();
  await expect(dialog.getByText(/could not reach that endpoint/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(dialog.getByRole("button", { name: "SAVE MODEL" })).toBeDisabled();

  await dialog.getByRole("button", { name: "CANCEL" }).click();
  await expect(dialog).toBeHidden();
});

test("model registry rows show the local/hosted privacy badge", async ({ page }) => {
  await page.goto("/system");

  // Built-in Claude entries are hosted; the badge is what tells a user
  // whether their notes leave the machine.
  const row = page.getByText("claude-sonnet-5").first();
  await expect(row).toBeVisible();
  await expect(page.getByText("HOSTED").first()).toBeVisible();
  await expect(page.getByText("DEFAULT").first()).toBeVisible();
});

test("study tab carries the same model selector as chat", async ({ page }) => {
  await page.goto("/study");
  // Study generation is a model call too — the selector would be misleading
  // if it governed only chat. Scoped to the page body: the top bar now carries
  // the same trigger on every route, so an unscoped locator matches both.
  await expect(page.locator("main").getByRole("button", { name: /^Model: / })).toBeVisible();
});

test("the engine picker groups models by whether they leave this machine", async ({ page }) => {
  await page.goto("/dashboard");
  // getByRole("banner") is the top bar specifically — pages have their own
  // <header> elements, but only a top-level one maps to the banner role.
  const trigger = page.getByRole("banner").getByRole("button", { name: /^Model: / });
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "Select engine" });
  await expect(dialog).toBeVisible();

  // The grouping *is* the privacy statement, so it has to be real: the
  // built-in Claude entries are network calls and must not be filed under
  // "on this computer".
  await expect(dialog.getByText("Sent to a provider")).toBeVisible();
  await expect(dialog.getByRole("radio", { name: /claude-sonnet-5/ })).toBeVisible();

  // Escape closes it and hands focus back to whatever opened it.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
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
