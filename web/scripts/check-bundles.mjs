/**
 * Perf budget: every route's First Load JS must stay under BUDGET_KB.
 * Parses `next build` output (the only place Next reports per-route size).
 */
import { execSync } from "node:child_process";

const BUDGET_KB = 135;

const out = execSync("npx next build", { encoding: "utf-8", stdio: ["ignore", "pipe", "inherit"] });
console.log(out);

const failures = [];
for (const line of out.split("\n")) {
  // e.g. "├ ○ /dashboard    12.3 kB    128 kB"
  const match = line.match(/[○ƒλ●]\s+(\/\S*)\s+[\d.]+\s*k?B\s+([\d.]+)\s*kB/);
  if (!match) continue;
  const [, route, firstLoad] = match;
  if (parseFloat(firstLoad) > BUDGET_KB) failures.push(`${route}: ${firstLoad} kB > ${BUDGET_KB} kB`);
}

if (failures.length > 0) {
  console.error(`\nPerf budget FAILED:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log(`\nPerf budget OK — all routes ≤ ${BUDGET_KB} kB first-load JS.`);
