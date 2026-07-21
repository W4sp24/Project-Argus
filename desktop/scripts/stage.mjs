/**
 * Assemble desktop/resources/ — everything electron-builder ships as
 * extraResources.
 *
 *   resources/backend/   PyInstaller onedir output (built separately)
 *   resources/web/       Next standalone server + static + our bootstrap
 *   resources/models/    pre-baked embedding weights (optional, CI populates)
 *
 * The Next part is the fiddly bit: `output: 'standalone'` traces the server
 * but does NOT copy .next/static or public/, so a bundle that looks complete
 * serves HTML with no CSS or JS. That copy is the single most commonly missed
 * step in shipping a standalone Next app.
 */

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.join(here, "..");
const repo = path.join(desktop, "..");
const web = path.join(repo, "web");
const resources = path.join(desktop, "resources");

const skipBuild = process.argv.includes("--no-build");

/**
 * Emit a GitHub Actions error annotation. Raw step logs are only visible to
 * signed-in users; annotations show on the run summary page, so this is what
 * makes a CI failure diagnosable from outside the repo. No-op locally.
 */
function annotate(message) {
  if (!process.env.GITHUB_ACTIONS) return;
  const flat = String(message).replace(/\s+/g, " ").slice(0, 900);
  console.log(`::error title=stage::${flat}`);
}

// Any uncaught throw (a failed npm build, a bad copy) surfaces as an
// annotation rather than a bare "exit code 1".
process.on("uncaughtException", (error) => {
  annotate(error?.message ?? String(error));
  console.error(error);
  process.exit(1);
});

async function copyDir(from, to) {
  await fsp.cp(from, to, { recursive: true, force: true });
}

function need(target, hint) {
  if (!fs.existsSync(target)) {
    console.error(`missing: ${target}\n  ${hint}`);
    annotate(`missing: ${target} - ${hint}`);
    process.exit(1);
  }
}

// --- Next ------------------------------------------------------------------

if (!skipBuild) {
  console.log("building the dashboard (ARGUS_PACKAGED=1)…");
  // On Windows npm is npm.cmd, and since the CVE-2024-27980 fix (Node
  // >=18.20.2 / 20.12.2) spawning a .cmd without a shell throws EINVAL. So
  // shell:true is required here, not optional. It emits DEP0190 because a
  // shell concatenates args unescaped — harmless in this case since the args
  // are fixed literals with nothing interpolated into them.
  const isWindows = process.platform === "win32";
  execFileSync(isWindows ? "npm.cmd" : "npm", ["run", "build"], {
    cwd: web,
    stdio: "inherit",
    shell: isWindows,
    env: { ...process.env, ARGUS_PACKAGED: "1" },
  });
}

const standalone = path.join(web, ".next", "standalone");
need(
  path.join(standalone, "server.js"),
  "run `npm run build` in web/ with ARGUS_PACKAGED=1 (output: 'standalone')",
);

const webOut = path.join(resources, "web");
await fsp.rm(webOut, { recursive: true, force: true });
await fsp.mkdir(webOut, { recursive: true });
await copyDir(standalone, webOut);

// The two things standalone leaves behind.
await copyDir(path.join(web, ".next", "static"), path.join(webOut, ".next", "static"));
if (fs.existsSync(path.join(web, "public"))) {
  await copyDir(path.join(web, "public"), path.join(webOut, "public"));
}

// Parent-death guard, kept outside app.asar so plain Node can require it.
await fsp.copyFile(
  path.join(desktop, "lib", "next-bootstrap.js"),
  path.join(webOut, "argus-bootstrap.js"),
);

// --- embedding model -------------------------------------------------------
// CI pre-bakes the weights here so a fresh install never depends on a
// HuggingFace fetch. Locally the directory is usually empty, and the app
// handles that by falling back to downloading on first index (paths.js
// returns null, so ARGUS_EMBED_MODEL/HF_HUB_OFFLINE are simply not set).
// It still has to exist, because electron-builder errors on a missing
// extraResources source.
const modelsDir = path.join(resources, "models");
await fsp.mkdir(modelsDir, { recursive: true });
if (fs.readdirSync(modelsDir).length === 0) {
  await fsp.writeFile(
    path.join(modelsDir, "README.txt"),
    "Populated by CI with BAAI/bge-small-en-v1.5.\n" +
      "When empty, Argus downloads the model on first index instead.\n",
    "utf8",
  );
}

// --- report ----------------------------------------------------------------

function sizeMb(target) {
  if (!fs.existsSync(target)) return null;
  let total = 0;
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else total += fs.statSync(full).size;
    }
  };
  walk(target);
  return (total / 1024 / 1024).toFixed(1);
}

console.log(`\nstaged into ${resources}`);
for (const name of ["backend", "web", "models"]) {
  const mb = sizeMb(path.join(resources, name));
  console.log(`  ${name.padEnd(8)} ${mb === null ? "(absent)" : `${mb} MB`}`);
}
if (!fs.existsSync(path.join(resources, "backend"))) {
  console.log(
    "\nnote: resources/backend is empty — build it with:\n" +
      "  pyinstaller desktop/argus-backend.spec --noconfirm --distpath desktop/resources",
  );
}
