/**
 * Perf budget: every route's First Load JS must stay under BUDGET_KB.
 * Parses `next build` output (the only place Next reports per-route size).
 */
import { execSync } from "node:child_process";

const BUDGET_KB = 135;

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

if (failures.length > 0) {
  console.error(`\nPerf budget FAILED:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log(`\nPerf budget OK — all routes ≤ ${BUDGET_KB} kB first-load JS.`);
