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

async function copyDir(from, to) {
  await fsp.cp(from, to, { recursive: true, force: true });
}

function need(target, hint) {
  if (!fs.existsSync(target)) {
    console.error(`missing: ${target}\n  ${hint}`);
    process.exit(1);
  }
}

// --- Next ------------------------------------------------------------------

if (!skipBuild) {
  console.log("building the dashboard (ARGUS_PACKAGED=1)…");
  // npm.cmd directly rather than shell:true — with a shell, args are
  // concatenated unescaped (Node DEP0190).
  execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "build"], {
    cwd: web,
    stdio: "inherit",
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
