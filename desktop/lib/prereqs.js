"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile } = require("node:child_process");

/**
 * Prerequisites Argus genuinely cannot bundle.
 *
 * Both are hard blocks, not warnings:
 *
 *  - Claude Code: backend/agent/{runtime,planner,generate}.py drive
 *    claude-agent-sdk, which spawns the Claude Code CLI and authenticates with
 *    the user's own Claude subscription. Chat, the planner, study generation
 *    and the morning briefing are all dead without it, and there is no API-key
 *    fallback (invariant I5).
 *  - git: backend/cli.py::_run_git shells out for `git init`/`add`/`commit`,
 *    so vault creation fails outright, and doctor reports vault-git FAIL --
 *    which means the writer has no pre-apply snapshots (invariant I2).
 */

function run(command, args) {
  return new Promise((resolve) => {
    execFile(command, args, { windowsHide: true, timeout: 8000 }, (error, stdout) => {
      resolve({ ok: !error, output: String(stdout || "").trim() });
    });
  });
}

async function checkGit() {
  const { ok, output } = await run("git", ["--version"]);
  return {
    id: "git",
    label: "Git for Windows",
    ok,
    detail: ok ? output : "not found on PATH",
    help: "Argus snapshots your vault with git before every change.",
    url: "https://git-scm.com/download/win",
  };
}

async function checkClaude() {
  // `where` finds the launcher; the ~/.claude directory is what tells us the
  // user has actually signed in. Both are needed -- an installed-but-logged-out
  // CLI fails later, deep inside a chat turn, with a confusing error.
  const found = await run("where", ["claude"]);
  const home = fs.existsSync(path.join(os.homedir(), ".claude"));
  const ok = found.ok && home;
  let detail = "installed and signed in";
  if (!found.ok) detail = "not found on PATH";
  else if (!home) detail = "installed, but no ~/.claude — run `claude` once to sign in";
  return {
    id: "claude",
    label: "Claude Code",
    ok,
    detail,
    help: "Chat, the planner, study generation and briefings run on your own Claude subscription.",
    url: "https://claude.com/code",
  };
}

async function checkAll() {
  return Promise.all([checkClaude(), checkGit()]);
}

module.exports = { checkAll };
