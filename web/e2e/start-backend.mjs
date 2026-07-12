/**
 * E2E backend launcher: provision a throwaway vault, seed one pending
 * suggestion, then run uvicorn from a workdir whose .env points at it.
 * The real vault is never touched; port 8000 being busy aborts the run.
 */
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const python = path.join(root, ".venv", "Scripts", "python.exe");
const workdir = path.join(here, ".workdir");
const vault = path.join(workdir, "vault");
const envFile = path.join(workdir, ".env");

fs.rmSync(workdir, { recursive: true, force: true });
fs.mkdirSync(workdir, { recursive: true });

execFileSync(python, ["-m", "backend.cli", "init", vault, "--env-file", envFile], {
  cwd: root,
  stdio: "inherit",
});

fs.writeFileSync(
  path.join(vault, "20-Projects", "e2e.md"),
  "# E2E\n\n- [ ] Move the meeting 📅 2026-07-20\n",
  "utf-8",
);

execFileSync(python, [path.join(here, "seed_suggestion.py"), vault], {
  cwd: root,
  stdio: "inherit",
});

const server = spawn(python, ["-m", "uvicorn", "backend.main:app", "--port", "8000"], {
  cwd: workdir,
  stdio: "inherit",
});
server.on("exit", (code) => process.exit(code ?? 0));
