import { expect, type Page, test } from "@playwright/test";

/**
 * Locates a `Panel` header row by its eyebrow label (e.g. "AGENT.USAGE").
 *
 * Matched by class rather than by the exact header markup: `Panel.tsx`'s
 * header div is `mb-1 flex items-center …` both before and after the
 * flex-wrap fix, so this selector keeps working whether or not the fix is
 * present — which is what lets the same helper prove the assertion goes red
 * on the unfixed code and green on the fixed code.
 *
 * Matched against `▍${label}` (the eyebrow's actual rendered text, tick
 * included), not the bare label — "MODELS" alone also matches the unrelated
 * "LOCAL.MODELS" panel's header, since its plain text is
 * "▍LOCAL.MODELS" and Playwright's `hasText` is substring-based.
 */
function headerRow(page: Page, label: string) {
  return page.locator("div.mb-1.flex.items-center", { hasText: `▍${label}` });
}

/** No panel header may need more width than the panel gives it. */
async function expectHeaderFits(page: Page, label: string) {
  const header = headerRow(page, label);
  await expect(header).toBeVisible();
  const { scrollWidth, clientWidth } = await header.evaluate((el) => ({
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
  }));
  expect(scrollWidth, `${label} header overflows its panel`).toBeLessThanOrEqual(clientWidth + 1);
}

/** Applies the DISPLAY panel's large scale the way the app does — via the
 * localStorage key `layout.tsx`'s boot script reads before first paint —
 * rather than poking `data-ui-scale` on directly, which would skip the same
 * code path a real user's setting takes. */
async function useLargeUiScale(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("argus-ui-scale", "large");
  });
}

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

/**
 * AGENT.USAGE and ARGUS.USAGE both sit in the 21.25rem right rail on
 * /dashboard (and /code). AGENT.USAGE's `headerRight` used to carry a
 * `+ ADD AGENT` button *and* a bare range switcher — together wider than the
 * rail's ~300px content box after `Panel`'s `p-5` padding, which pushed the
 * header past the panel edge instead of wrapping.
 */
test("rail panel headers fit their panel on /dashboard at 1280x800", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/dashboard");

  await expectHeaderFits(page, "AGENT.USAGE");
  await expectHeaderFits(page, "ARGUS.USAGE");
});

/**
 * Every size token in this app is rem-based, so the large UI scale (18px
 * root, set from /system → DISPLAY) scales the rail and its contents
 * together — but text metrics don't scale in perfect lockstep with
 * container width, so this is the setting under which the header overflow
 * was reported as worst.
 */
test("rail panel headers fit their panel on /dashboard at the large UI scale", async ({ page }) => {
  await useLargeUiScale(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/dashboard");
  await expect(page.locator("html")).toHaveAttribute("data-ui-scale", "large");

  await expectHeaderFits(page, "AGENT.USAGE");
  await expectHeaderFits(page, "ARGUS.USAGE");
});

/**
 * MODELS, INTEGRATIONS and DOCTOR live on /system and each carry a single
 * `headerRight` button (+ ADD MODEL / + ADD MCP SERVER / RUN AGAIN) — the
 * `Panel` header fix (`flex-wrap`, `min-w-0`) must not regress these while
 * fixing AGENT.USAGE.
 *
 * UPDATES also uses `headerRight`, but `UpdatesPanel` renders nothing outside
 * the packaged Electron shell (no `window.argus` bridge in a plain browser),
 * so it never mounts in this e2e run and is not asserted here.
 */
test("panel headers fit their panel on /system", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/system");

  await expectHeaderFits(page, "DOCTOR");
  await expectHeaderFits(page, "INTEGRATIONS");
  await expectHeaderFits(page, "MODELS");
  await expectHeaderFits(page, "AGENT.USAGE");
});

test("panel headers fit their panel on /system at the large UI scale", async ({ page }) => {
  await useLargeUiScale(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/system");
  await expect(page.locator("html")).toHaveAttribute("data-ui-scale", "large");

  await expectHeaderFits(page, "DOCTOR");
  await expectHeaderFits(page, "INTEGRATIONS");
  await expectHeaderFits(page, "MODELS");
  await expectHeaderFits(page, "AGENT.USAGE");
});

/**
 * Nothing may make the document scroll sideways on a phone.
 *
 * At 390px (iPhone 12/13/14 CSS width) the document measured 620px — 230px of
 * overhang, which pushed the `open ↗` link off the right edge of every row on
 * /sources: present in the DOM, unreachable with a thumb. The cause was the
 * top bar, not the pages: six mode tabs plus the logo plus the utility cluster
 * cannot fit 390px, and every one of them was a flex item with the default
 * `min-width: auto`, so the strip grew the header instead of yielding.
 *
 * Everything here is asserted from a single page load, deliberately. The top
 * bar is the same component on every route, so extra routes would re-prove one
 * fact — and this suite shares one backend with a real vault, where each extra
 * navigation is measurable contention: an earlier draft that swept three
 * routes pushed `POST /api/ingest/jobs` in sources.spec past its five-second
 * expect while SQLite was locked by the dashboard's `refresh_cache`.
 */
test("the app does not scroll horizontally at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  // /sources rather than /dashboard: it is the route where the clipped
  // `open ↗` was actually reported, and the cheapest of the two to serve.
  await page.goto("/sources");
  // Waited on rather than the static heading: "All folders" only renders once
  // `GET /api/sources` has come back, so the vault scan is finished before
  // this test ends. Tearing the page down mid-scan leaves the shared backend
  // still working, and the next spec's ingest POST then queues behind it.
  await expect(page.getByRole("button", { name: "All folders" })).toBeVisible();

  // The measurement names its own culprit. A bare `scrollWidth` number says a
  // page is too wide and nothing about which element made it so, and this
  // assertion is one that can fail on a runner nobody can attach a debugger
  // to — CI's raw logs need a login, so the failure message is the whole of
  // the evidence. Reported here: the outermost elements that stick out past
  // the viewport, which is where a missing `min-w-0` actually lives.
  const { scrollWidth, offenders } = await page.evaluate(() => {
    const vw = 390;
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
      const box = el.getBoundingClientRect();
      if (box.width === 0 && box.height === 0) continue;
      const over = Math.round(box.right - vw);
      if (over <= 0) continue;
      // Skip anything whose parent already overflows at least as far: the
      // child is being dragged along, and reporting it buries the cause.
      const parent = el.parentElement;
      if (parent && Math.round(parent.getBoundingClientRect().right - vw) >= over) continue;
      const cls = `${el.className || ""}`.split(/\s+/).slice(0, 4).join(" ");
      out.push(`+${over}px <${el.tagName.toLowerCase()} class="${cls}">`);
    }
    return { scrollWidth: document.documentElement.scrollWidth, offenders: out.slice(0, 4) };
  });
  expect(
    scrollWidth,
    `the document overflows 390px by ${scrollWidth - 390}px; widest offenders: ${
      offenders.join(" | ") || "none measurable"
    }`,
  ).toBeLessThanOrEqual(390);

  // The fix works by letting the tab strip scroll inside itself, so all six
  // modes must still be there — a fix that simply dropped tabs would satisfy
  // the assertion above and lose a third of the app's navigation.
  const tabs = page.getByRole("tablist", { name: "Mode" }).getByRole("tab");
  await expect(tabs).toHaveCount(6);

  // ...and each is named in full at every width. The tab renders "GE" below
  // `md` and "GENERAL" above it; before `aria-label` the accessible name was
  // whichever one the breakpoint left standing, so a phone user heard "GE".
  for (const name of ["GENERAL", "STUDY", "RESEARCH", "CODE", "SYSTEM", "AUTO"]) {
    await expect(page.getByRole("tab", { name, exact: true })).toHaveCount(1);
  }
});

test("the course hub shows one pane at a time on a narrow screen", async ({ page }) => {
  // Below `lg` the three panes stacked inside a fixed-height column, so each
  // got about a third of the viewport with its own scrollbar nested inside
  // the page scroll. Tabs give whichever pane you are using the whole height.
  await page.setViewportSize({ width: 820, height: 900 });
  await page.goto("/study/course/CS000");

  const tabs = page.getByRole("tablist", { name: "Course hub pane" });
  await expect(tabs).toBeVisible();

  const sources = page.locator("section").filter({ hasText: "▍SOURCES" });
  const studio = page.locator("section").filter({ hasText: "▍STUDIO" });
  await expect(sources).toBeHidden();

  await tabs.getByRole("tab", { name: "sources" }).click();
  await expect(sources).toBeVisible();
  await expect(studio).toBeHidden();

  // Wide again: all three are back side by side, no tabs.
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(tabs).toBeHidden();
  await expect(sources).toBeVisible();
  await expect(studio).toBeVisible();
});
