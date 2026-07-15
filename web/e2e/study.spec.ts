import { expect, test } from "@playwright/test";

test("study sub-nav deep-links between overview, flashcards, and exam", async ({ page }) => {
  await page.goto("/study");
  await expect(page.getByRole("tab", { name: "OVERVIEW" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("▍COURSES")).toBeVisible(); // exact panel eyebrow — "COURSES" alone matches 3 nodes
  // Seeded vault course. `.first()`: "CS000" appears twice since Phase H —
  // the course card AND the shared ingest dropzone's upload-target <option>.
  await expect(page.getByText("CS000").first()).toBeVisible();

  await page.getByRole("tab", { name: "FLASHCARDS" }).click();
  await expect(page).toHaveURL(/\/study\/flashcards$/);
  await expect(page.getByText("DECK.MANAGE")).toBeVisible();
  await expect(page.getByText("STUDY.SESSION")).toBeVisible();

  await page.getByRole("tab", { name: "PRACTICE EXAM" }).click();
  await expect(page).toHaveURL(/\/study\/exam$/);
  await expect(page.getByText("PRACTICE.EXAM")).toBeVisible();
  await expect(page.getByText("SCORES.HISTORY")).toBeVisible();

  // Deep link directly to a sub-page and back to overview.
  await page.goto("/study/flashcards");
  await expect(page.getByRole("tab", { name: "FLASHCARDS" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "OVERVIEW" }).click();
  await expect(page).toHaveURL(/\/study$/);
});

test("flashcard flip and grading advance the mock study session", async ({ page }) => {
  await page.goto("/study/flashcards");
  const front = page.getByTestId("flashcard-front");
  await expect(front).toContainText("What is Big-O of binary search?");
  // Both faces stay mounted (CSS backface-visibility, not display:none), so
  // flip state is asserted structurally via the inner wrapper's class, not
  // text visibility — Playwright's visibility check doesn't model 3D backfaces.
  const inner = page.getByTestId("flashcard-inner");
  await expect(inner).not.toHaveClass(/is-flipped/);
  await front.click();
  await expect(inner).toHaveClass(/is-flipped/);

  await page.getByRole("button", { name: "GOOD" }).click();
  await expect(page.getByText(/scheduled :: good in 3d/)).toBeVisible();
});

test("course hub opens from a course row and links back", async ({ page }) => {
  await page.goto("/study");
  await page.getByRole("link", { name: "HUB →" }).click();
  await expect(page).toHaveURL(/\/study\/course\/CS000$/);
  await expect(page.getByText("COURSE.HUB · CS000")).toBeVisible();
  // "Sample Course" is ambiguous here by design: course.md (the hub note
  // itself, title "Sample Course") lives inside 15-Courses/CS000/ and so
  // also shows up as a SOURCES row — scope to the header to pick the title.
  await expect(page.locator("header").getByText("Sample Course")).toBeVisible();

  await page.getByRole("button", { name: "← BACK" }).click();
  await expect(page).toHaveURL(/\/study$/);
});
