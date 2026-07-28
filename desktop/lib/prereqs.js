"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile } = require("node:child_process");

/**
 * Prerequisites Argus cannot bundle.
 *
 * Only **git** is a hard block now: backend/cli.py::_run_git shells out for
 * `git init`/`add`/`commit`, so vault creation fails outright without it and
 * the writer has no pre-apply snapshots (invariant I2).
 *
 * Claude Code used to be a hard block too, because the agent ran exclusively
 * through claude-agent-sdk. It no longer does — backend/agent/adapters.py
 * routes chat, the planner and study generation to local Ollama models, to any
 * hosted OpenAI-compatible provider, or to the Anthropic API on a key, with
 * Claude Code as one option among those. Blocking on it would now turn a
 * working install into a dead end for anyone who does not want it.
 *
 * Optional checks still report their state, so the wizard can tell someone
 * *why* a path is unavailable rather than leaving them to find out later.
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
    required: true,
    ok,
    detail: ok ? output : "not found on PATH",
    help: "Argus snapshots your vault with git before every change.",
    url: "https://git-scm.com/download/win",
  };
}

async function checkClaude() {
  // `where` finds the launcher; the ~/.claude directory is what tells us the
  // user has actually signed in. An installed-but-logged-out CLI fails later,
  // deep inside a chat turn, with a confusing error.
  const found = await run("where", ["claude"]);
  const home = fs.existsSync(path.join(os.homedir(), ".claude"));
  const ok = found.ok && home;
  let detail = "installed and signed in";
  if (!found.ok) detail = "not installed — optional";
  else if (!home) detail = "installed, but no ~/.claude — run `claude` once to sign in";
  return {
    id: "claude",
    label: "Claude Code",
    required: false,
    ok,
    detail,
    help: "One way to run Argus's AI, using your existing Claude subscription.",
    url: "https://claude.com/code",
  };
}

async function checkOllama() {
  // Two signals, same reasoning as Claude Code: the CLI on PATH proves it is
  // installed, and a reachable API port proves it is actually running. A
  // stopped Ollama fails at the first question, not at setup.
  const found = await run("where", ["ollama"]);
  const running = await probeOllama();
  const ok = found.ok && running;
  let detail = "installed and running";
  if (!found.ok) detail = "not installed — optional";
  else if (!running) detail = "installed, but not running — start Ollama";
  return {
    id: "ollama",
    label: "Ollama",
    required: false,
    ok,
    detail,
    help: "Runs AI models on this computer, free and offline. Your notes never leave.",
    url: "https://ollama.com/download",
  };
}

function probeOllama() {
  return new Promise((resolve) => {
    const http = require("node:http");
    const request = http.get(
      { host: "127.0.0.1", port: 11434, path: "/api/tags", timeout: 2000 },
      (response) => {
        response.resume();
        resolve(response.statusCode < 400);
      },
    );
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function checkAll() {
  return Promise.all([checkGit(), checkClaude(), checkOllama()]);
}

module.exports = { checkAll };
