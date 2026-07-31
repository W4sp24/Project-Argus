#!/usr/bin/env node
/**
 * Assert pyproject.toml, web/package.json and desktop/package.json agree.
 *
 * Only desktop/package.json is authoritative for the shipped artifact --
 * app.getVersion(), electron-updater's comparison and the installer filename
 * all derive from it -- so the other two drifted unnoticed: both said 0.1.0
 * while v0.2.0 was the published tag. Nothing checked, so nothing complained.
 *
 * CI rewrites desktop/package.json from the tag before packaging
 * (release.yml's `npm version --no-git-tag-version`), so during a tag build
 * the three legitimately differ for one step. GITHUB_REF_NAME is how we tell
 * that apart from real drift.
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");

const readJson = (rel) => JSON.parse(readFileSync(join(REPO, rel), "utf8"));

function pyprojectVersion() {
  const text = readFileSync(join(REPO, "pyproject.toml"), "utf8");
  // The first `version = "..."` under [project]; build-system has no such key.
  const match = text.match(/^version\s*=\s*"([^"]+)"/m);
  if (!match) throw new Error("no version field in pyproject.toml");
  return match[1];
}

const found = {
  "pyproject.toml": pyprojectVersion(),
  "web/package.json": readJson("web/package.json").version,
  "desktop/package.json": readJson("desktop/package.json").version,
};

const tag = process.env.GITHUB_REF_NAME;
if (tag && tag.startsWith("v")) {
  // A tag build: desktop/ is rewritten from the tag, so compare against that
  // rather than against its committed value.
  const expected = tag.slice(1);
  const wrong = Object.entries(found).filter(([, v]) => v !== expected);
  if (wrong.length) {
    console.error(`version mismatch against tag ${tag} (expected ${expected}):`);
    for (const [file, v] of wrong) console.error(`  ${file}: ${v}`);
    console.error("\nBump all three in the release commit, or retag.");
    process.exit(1);
  }
  console.log(`versions agree with ${tag}: ${expected}`);
  process.exit(0);
}

const distinct = new Set(Object.values(found));
if (distinct.size !== 1) {
  console.error("version drift between the three manifests:");
  for (const [file, v] of Object.entries(found)) console.error(`  ${file}: ${v}`);
  console.error(
    "\nOnly desktop/package.json reaches the installed app, which is exactly why\n" +
      "the other two can rot unnoticed. Set all three to the same value.",
  );
  process.exit(1);
}

console.log(`versions agree: ${[...distinct][0]}`);
