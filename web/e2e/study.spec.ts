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
  // Real wording (Flashcards.tsx): "scheduled :: <grade> — next due <date>" —
  // the exact due date is FSRS-computed and locale-formatted, so only the
  // fixed prefix is asserted.
  await expect(page.getByText(/scheduled :: good — next due/)).toBeVisible();
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

// Regression test for the reported bug: "Study … still retains sample data
// after it is deleted." The × button used to only hide the row in local
// React state — the course came right back on the next reload, route
// change, or app restart. The reload below is the whole point of this test.
test("deleting a course removes it permanently, even after a reload", async ({ page }) => {
  await page.goto("/study");

  await page.getByRole("button", { name: "+ ADD COURSE" }).click();
  await page.getByLabel("Course code").fill("CS999");
  await page.getByLabel("Course name").fill("Delete Me");
  await page.getByRole("button", { name: "ADD", exact: true }).click();
  await expect(page.getByText("CS999").first()).toBeVisible();

  await page.getByRole("button", { name: "Delete CS999" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Delete CS999" });
  await expect(confirmDialog).toBeVisible();
  await confirmDialog.getByRole("button", { name: "DELETE" }).click();
  await expect(page.getByText("CS999").first()).not.toBeVisible();

  await page.reload();
  await expect(page.getByText("CS999").first()).not.toBeVisible();
});

// Regression test for the reported bug: "Study — ingesting files seems to
// not work." IngestPanel's upload target used to be the course *root*
// (`15-Courses/<code>`), not the real `materials/` folder the backend
// reports via `CourseInfo.materials_path` — the file saved fine, but
// courses() only ever counts files inside materials/, so the row stayed at
// "0 materials" and GUIDE/EXAM never left disabled. CS000 (seeded by
// start-backend.mjs) starts with zero materials, so GUIDE starting disabled
// and becoming enabled after one upload is the whole proof.
test("uploading through the shared ingest panel lands in materials/ and unlocks GUIDE/EXAM", async ({
  page,
}) => {
  await page.goto("/study");

  const guideButton = page.getByRole("button", { name: "GUIDE" });
  await expect(guideButton).toBeDisabled();

  await page.getByLabel("upload target").selectOption("CS000");
  // Two file inputs exist on this page: CS000's own row-level "+ FILES"
  // input (CoursesPanel) and the shared ingest dropzone's — this is the
  // latter, the one actually driven by the "upload target" selector above.
  await page.locator('input[type="file"]').last().setInputFiles({
    name: "syllabus.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake pdf bytes"),
  });

  await expect(page.getByText(/done :: syllabus\.pdf indexed|saved/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(guideButton).toBeEnabled();
  await expect(page.getByRole("button", { name: "+ EXAM" })).toBeEnabled();
});
