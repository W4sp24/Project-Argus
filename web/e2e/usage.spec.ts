import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * AGENT.USAGE, on the two narrow-rail routes it actually ships on.
 *
 * The suite had no coverage of this panel at all — `dashboard.spec.ts` only
 * asserted that the sibling ARGUS.USAGE panel was visible — which is how it
 * came to overflow its container on both GENERAL and CODE without anything
 * failing. The containment checks are geometric for that reason: nothing in a
 * type system or a unit test can see a menu escaping a panel.
 */

/** The `<section>` a panel renders, located by its ▍LABEL eyebrow. */
function panel(page: Page, label: string): Locator {
  return page
    .locator("section")
    .filter({ hasText: `▍${label}` })
    .last();
}

/**
 * Panel and menu rectangles, read in one pass.
 *
 * Two separate `boundingBox()` calls race SWR's revalidation re-render: the
 * first can resolve against an element the second no longer finds attached.
 * Reading both inside one evaluate makes the measurement a single instant.
 */
async function geometry(page: Page, label: string) {
  return page.evaluate((eyebrow) => {
    const matches = Array.from(document.querySelectorAll("section")).filter((section) =>
      section.textContent?.includes(`▍${eyebrow}`),
    );
    // Last match, not first: an ancestor <section> would sort earlier.
    const section = matches[matches.length - 1];
    if (!section) return null;
    const menu = section.querySelector('[role="listbox"]');
    const root = document.documentElement;
    return {
      panelRight: section.getBoundingClientRect().right,
      menuRight: menu ? menu.getBoundingClientRect().right : null,
      pageOverflows: root.scrollWidth > root.clientWidth + 1,
    };
  }, label);
}

for (const route of ["/dashboard", "/code"]) {
  test(`agent usage fits inside the rail on ${route}`, async ({ page }) => {
    await page.goto(route);

    const usage = panel(page, "AGENT.USAGE");
    await expect(usage).toBeVisible();

    // The header carries a label, + ADD AGENT and a three-way switcher, which
    // do not fit on one line in a 21.25rem rail. They have to wrap rather than
    // push out through the panel's right border.
    await expect(usage.getByRole("button", { name: "TODAY", exact: true })).toBeVisible();

    // The agent dropdown was 21rem wide inside ~18.75rem of content width.
    await usage.getByRole("button", { name: /All agents/i }).click();
    await expect(usage.getByRole("listbox", { name: "Agent" })).toBeVisible();

    const measured = await geometry(page, "AGENT.USAGE");
    expect(measured, "AGENT.USAGE should be on the page").not.toBeNull();
    expect(measured!.menuRight, "the menu should be open").not.toBeNull();
    // 1px of slack for sub-pixel layout rounding.
    expect(measured!.menuRight!).toBeLessThanOrEqual(measured!.panelRight + 1);
    expect(measured!.pageOverflows, "the rail must not widen the page").toBe(false);
  });
}

test("the agent usage range switcher is a real control", async ({ page }) => {
  await page.goto("/dashboard");
  const usage = panel(page, "AGENT.USAGE");

  const today = usage.getByRole("button", { name: "TODAY", exact: true });
  const week = usage.getByRole("button", { name: "WEEK", exact: true });

  await expect(today).toHaveAttribute("aria-pressed", "true");
  await week.click();
  await expect(week).toHaveAttribute("aria-pressed", "true");
  await expect(today).toHaveAttribute("aria-pressed", "false");

  // Whatever this machine's ~/.claude holds, the panel resolves to a real
  // state rather than sitting on the loading line.
  await expect(usage.getByText("loading usage…")).toHaveCount(0);
});

test("the agent selector keeps every agent reachable", async ({ page }) => {
  await page.goto("/dashboard");
  const usage = panel(page, "AGENT.USAGE");

  await usage.getByRole("button", { name: /All agents/i }).click();
  const menu = usage.getByRole("listbox", { name: "Agent" });
  await expect(menu).toBeVisible();

  // An agent that is not installed stays listed and inert rather than
  // disappearing — a selector that silently drops an option reads as a bug —
  // and any such row states its own reason, because it cannot be clicked to
  // reveal one anywhere else.
  await expect(menu.getByRole("option", { name: /All agents/i })).toBeVisible();
  const unavailable = menu.locator('[aria-disabled="true"]');
  for (let i = 0; i < (await unavailable.count()); i++) {
    await expect(unavailable.nth(i)).toContainText(/not detected/i);
  }

  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
});

test("argus usage no longer shows a budget it invented", async ({ page }) => {
  await page.goto("/system");
  const usage = panel(page, "ARGUS.USAGE");
  await expect(usage).toBeVisible();

  // The bar measured against a hardcoded 25k/175k/2M that nothing configures.
  await expect(usage.getByText(/soft cap/i)).toHaveCount(0);
});
