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
// ARGUS_PYTHON so CI can point at the runner's interpreter: a hosted runner
// has no repo-local .venv, and hardcoding one made this suite local-only --
// which is part of why nothing in web/ was ever gated by CI.
const python =
  process.env.ARGUS_PYTHON || path.join(root, ".venv", "Scripts", "python.exe");
const workdir = path.join(here, ".workdir");
const vault = path.join(workdir, "vault");
const envFile = path.join(workdir, ".env");

fs.rmSync(workdir, { recursive: true, force: true });
fs.mkdirSync(workdir, { recursive: true });

execFileSync(python, ["-m", "backend.cli", "init", vault, "--env-file", envFile], {
  cwd: root,
  stdio: "inherit",
});

function localToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

// "Move the meeting" must stay on line 3 verbatim — seed_suggestion.py below
// targets it by exact old_line text for the roundtrip apply test. Its due
// date (2026-07-20) is intentionally a fixed fixture date, not "today", so
// it lands outside the overdue/today agenda bucket and won't render on the
// dashboard; "E2E delete me" is a separate, always-due-today task for the
// dashboard delete-flow test.
fs.writeFileSync(
  path.join(vault, "20-Projects", "e2e.md"),
  `# E2E\n\n- [ ] Move the meeting 📅 2026-07-20\n- [ ] E2E check me off 📅 ${localToday()}\n- [ ] E2E delete me 📅 ${localToday()}\n- [x] already done ✅ ${localToday()}\n`,
  "utf-8",
);

// CS000 no longer ships as a sample course (backend/cli.py stopped copying
// it into fresh vaults — that permanent demo content was the reported
// "study still retains sample data after it is deleted" bug). study.spec.ts
// still exercises a real course end to end though, so the e2e vault grows
// its own CS000 here instead of inheriting one from the template.
fs.mkdirSync(path.join(vault, "15-Courses", "CS000"), { recursive: true });
fs.writeFileSync(
  path.join(vault, "15-Courses", "CS000", "course.md"),
  `---\ntype: course\ncode: CS000\ntitle: Sample Course\ncreated: "${localToday()}"\ntags: [course]\nstatus: active\n---\n\n# CS000 — Sample Course\n`,
  "utf-8",
);

execFileSync(python, [path.join(here, "seed_suggestion.py"), vault], {
  cwd: root,
  stdio: "inherit",
});

// Deterministic flashcard deck (no model call) — see seed_flashcards.py.
execFileSync(python, [path.join(here, "seed_flashcards.py"), vault], {
  cwd: root,
  stdio: "inherit",
});

// n8n automations fixtures (instances, widgets in all four states, cached
// workflows, activity events) — direct DB/registry writes, no keyring and no
// live n8n call. See seed_automations.py's module docstring for why the
// connect-an-instance UI path is never exercised in this suite.
execFileSync(python, [path.join(here, "seed_automations.py"), vault], {
  cwd: root,
  stdio: "inherit",
});

// Chat threads: a finished transcript with markdown and a tool trace, plus
// one thread per rail bucket. e2e has no live agent (the ws errors), so a
// real turn can never produce an answer to assert on -- see the module
// docstring in seed_chat_threads.py.
execFileSync(python, [path.join(here, "seed_chat_threads.py"), vault], {
  cwd: root,
  stdio: "inherit",
});

const server = spawn(python, ["-m", "uvicorn", "backend.main:app", "--port", "8000"], {
  cwd: workdir,
  stdio: "inherit",
});
server.on("exit", (code) => process.exit(code ?? 0));
