/**
 * Perf budget: every route's First Load JS must stay under BUDGET_KB, and the
 * render-blocking CSS must stay under CSS_BUDGET_KB.
 *
 * The JS figure is parsed out of `next build` output, the only place Next
 * reports it per route. The CSS figure is measured off the emitted files
 * instead, because Next's route table does not report CSS at all — so this
 * script was blind to a whole category of regression. Adding KaTeX is exactly
 * that category: its stylesheet is 26 kB, and had it landed in the layout's
 * bundle it would have been 26 kB of render-blocking CSS on every route in
 * the app, for a feature two of them use, with the budget still printing OK.
 *
 * "Render-blocking" is the number that matters, so it is the one budgeted: the
 * stylesheets Next links from the layout, which the browser must fetch before
 * first paint. CSS that arrives with a `next/dynamic` chunk (KaTeX's does)
 * costs nothing until that chunk loads, and is reported but not budgeted.
 */
import { execSync } from "node:child_process";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const BUDGET_KB = 135;
// Headroom over the current figure, not a round number: the point is to catch
// the next unnoticed stylesheet, not to leave room for one.
const CSS_BUDGET_KB = 60;

const rawOut = execSync("npx next build", { encoding: "utf-8", stdio: ["ignore", "pipe", "inherit"] });
console.log(rawOut);
// Next colorizes this table even when stdout is piped (observed on 14.2.35) —
// strip ANSI escapes first or the size columns splice apart mid-match.
// eslint-disable-next-line no-control-regex
const out = rawOut.replace(/\x1b\[[0-9;]*m/g, "");

const failures = [];
let matchedAny = false;
for (const line of out.split("\n")) {
  // e.g. "├ ○ /dashboard    12.3 kB    128 kB"
  const match = line.match(/[○ƒλ●]\s+(\/\S*)\s+[\d.]+\s*k?B\s+([\d.]+)\s*kB/);
  if (!match) continue;
  matchedAny = true;
  const [, route, firstLoad] = match;
  if (parseFloat(firstLoad) > BUDGET_KB) failures.push(`${route}: ${firstLoad} kB > ${BUDGET_KB} kB`);
}

if (!matchedAny) {
  console.error(
    "\nPerf budget check parsed 0 routes from next build output — the parser regex may be stale for this Next.js version.",
  );
  process.exit(1);
}

const cssDir = join(".next", "static", "css");
const kb = (bytes) => Math.round((bytes / 1024) * 10) / 10;

let blockingKb = 0;
let lazyKb = 0;
try {
  // app-build-manifest lists, per route, the files Next links in the document.
  // Anything under static/css it does *not* name is pulled in by a lazily
  // loaded chunk instead, and so is not on the first-paint path.
  const manifest = JSON.parse(readFileSync(join(".next", "app-build-manifest.json"), "utf-8"));
  const linked = new Set(
    Object.values(manifest.pages)
      .flat()
      .filter((file) => file.endsWith(".css"))
      .map((file) => file.split("/").pop()),
  );
  for (const name of readdirSync(cssDir)) {
    if (!name.endsWith(".css")) continue;
    const size = statSync(join(cssDir, name)).size;
    if (linked.has(name)) blockingKb += size;
    else lazyKb += size;
  }
} catch (error) {
  console.error(`\nPerf budget check could not measure CSS: ${error.message}`);
  process.exit(1);
}

console.log(
  `\nCSS: ${kb(blockingKb)} kB render-blocking (budget ${CSS_BUDGET_KB} kB)` +
    `, ${kb(lazyKb)} kB in lazy chunks (unbudgeted)`,
);
if (kb(blockingKb) > CSS_BUDGET_KB) {
  failures.push(`render-blocking CSS: ${kb(blockingKb)} kB > ${CSS_BUDGET_KB} kB`);
}

if (failures.length > 0) {
  console.error(`\nPerf budget FAILED:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log(`\nPerf budget OK — all routes ≤ ${BUDGET_KB} kB first-load JS, CSS ≤ ${CSS_BUDGET_KB} kB.`);
