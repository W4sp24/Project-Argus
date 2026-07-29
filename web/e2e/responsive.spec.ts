import { expect, test } from "@playwright/test";

/**
 * The packaged Electron shell pins `minWidth: 960` (desktop/main.js), but the
 * app is also served in a plain browser — where the top bar used to simply
 * `hidden` its + NOTE / focus timer / CHAT cluster below 768px, removing those
 * actions from the app rather than relocating them.
 *
 * The rest of the suite runs at Playwright's default 1280x720, so nothing else
 * exercises a narrow viewport.
 */
test("narrow windows relocate the top bar's actions instead of dropping them", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await page.goto("/dashboard");

  const banner = page.getByRole("banner");

  // The engine chip never collapses: which model is answering, and whether it
  // runs locally, is not something to hide at a breakpoint.
  await expect(banner.getByRole("button", { name: /^Model: / })).toBeVisible();

  // The desktop cluster is gone at this width...
  await expect(banner.getByRole("button", { name: "+ NOTE" })).toBeHidden();

  // ...and its actions are in the overflow menu.
  await banner.getByRole("button", { name: "More actions" }).click();
  const menu = page.getByRole("menu", { name: "More actions" });
  await expect(menu.getByRole("menuitem", { name: "+ NOTE" })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "CHAT" })).toBeVisible();

  // And they still do the thing they name.
  await menu.getByRole("menuitem", { name: "+ NOTE" }).click();
  await expect(page.getByRole("dialog", { name: "Add note" })).toBeVisible();
});

test("the engine picker is usable at a narrow width", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await page.goto("/dashboard");

  await page.getByRole("banner").getByRole("button", { name: /^Model: / }).click();
  const dialog = page.getByRole("dialog", { name: "Select engine" });
  await expect(dialog).toBeVisible();

  // The card is `w-[42.5rem] max-w-[calc(100vw-2rem)]`, so it must shrink to
  // fit rather than pushing the page into a horizontal scroll.
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThanOrEqual(720 - 32);

  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(720);
});
