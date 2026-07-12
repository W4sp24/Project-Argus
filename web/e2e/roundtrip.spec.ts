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

test("capture → approve → vault roundtrip", async ({ page }) => {
  // 1. Quick capture through the Today page lands in 00-Inbox via the writer.
  await page.goto("/today");
  await page.getByPlaceholder("e.g. email prof about thesis").fill("e2e roundtrip note");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Captured/)).toBeVisible();

  const captureNote = path.join(vault, "00-Inbox", `capture-${localToday()}.md`);
  await expect.poll(() => fs.readFileSync(captureNote, "utf-8")).toContain("e2e roundtrip note");

  // 2. The seeded task suggestion is approved on the Review page and the
  //    writer edits the note — after a pre-apply git snapshot (I2).
  await page.goto("/review");
  await expect(page.getByText("E2E roundtrip: push the meeting to the 22nd")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();

  const target = path.join(vault, "20-Projects", "e2e.md");
  await expect.poll(() => fs.readFileSync(target, "utf-8")).toContain("2026-07-22");
  expect(fs.readFileSync(target, "utf-8")).not.toContain("2026-07-20");

  const gitLog = execSync("git log --oneline", { cwd: vault, encoding: "utf-8" });
  expect(gitLog).toContain("argus: pre-apply snapshot (apply suggestion");

  // The Argus log audit line landed in today's daily note.
  const daily = path.join(vault, "10-Daily", `${localToday()}.md`);
  await expect.poll(() => fs.readFileSync(daily, "utf-8")).toContain("## Argus log");
});
