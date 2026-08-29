// WCAG 2.x contrast audit for the text tokens that carry information.
//
// Run by hand: `node scripts/check-contrast.mjs` (exits non-zero on a fail).
// This lives outside Playwright deliberately: the numbers are a property of
// tailwind.config.ts, not of a rendered page, so asserting them in a browser
// would mean booting Next and the backend to re-measure a constant. Keeping it
// here means a token edit can be checked in under a second, with no ports.
//
// The failure this prevents: `ink-faint` was #5a4f82 — 2.79:1 on `void`,
// 2.70:1 on `panel` — while carrying every source row's metadata, the folder
// counts, the ingest privacy line and the empty-state guidance. A token that
// low is unreadable for a large fraction of users, and nothing in the build
// caught it.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

// Pull the hex values straight out of the Tailwind config rather than
// restating them, so this script cannot drift from the tokens it audits.
const config = readFileSync(join(here, "..", "tailwind.config.ts"), "utf8");

function token(name) {
  const match = config.match(
    new RegExp(String.raw`\b${name}:\s*"(#[0-9a-fA-F]{6})"`),
  );
  if (!match) throw new Error(`token "${name}" not found in tailwind.config.ts`);
  return match[1];
}

// WCAG 2.x relative luminance: sRGB channel -> linear light, then the
// 0.2126/0.7152/0.0722 weighting. https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
function luminance(hex) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4),
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

const void_ = token("void");
const panel = token("panel");

// Both backgrounds matter: source rows and the folder list sit inside `Panel`,
// which is lighter than the page, so `void` alone is the optimistic number.
const checks = [
  { name: "ink-faint", fg: token("faint"), min: 4.5 },
  { name: "ink-muted", fg: token("muted"), min: 4.5 },
  { name: "ink", fg: token("DEFAULT"), min: 4.5 },
];

let failed = false;
console.log(`backgrounds: void ${void_}  panel ${panel}\n`);
for (const check of checks) {
  const onVoid = contrast(check.fg, void_);
  const onPanel = contrast(check.fg, panel);
  const pass = onVoid >= check.min && onPanel >= check.min;
  if (!pass) failed = true;
  console.log(
    `${check.name.padEnd(10)} ${check.fg}  on void ${onVoid.toFixed(2)}:1` +
      `  on panel ${onPanel.toFixed(2)}:1  (AA ${check.min}:1) ${pass ? "PASS" : "FAIL"}`,
  );
}

process.exit(failed ? 1 : 0);
