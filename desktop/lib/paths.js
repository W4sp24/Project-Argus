"use strict";

const path = require("node:path");
const fs = require("node:fs");
const { app } = require("electron");

const REPO_ROOT = path.resolve(__dirname, "..", "..");

/**
 * Resource layout differs between a packaged install and a dev checkout.
 *
 *   packaged: <install>/resources/{backend,web,models}
 *   dev:      desktop/resources/{backend,web,models}   (populated by scripts/stage.mjs)
 *
 * Everything is resolved through here so nothing else has to care.
 */
function resourcesDir() {
  return app.isPackaged
    ? process.resourcesPath
    : path.join(__dirname, "..", "resources");
}

/** The frozen backend executable. */
function backendExe() {
  return path.join(resourcesDir(), "backend", "argus-backend.exe");
}

/** Next's standalone server entry. */
function nextServer() {
  return path.join(resourcesDir(), "web", "server.js");
}

/** Pre-baked embedding weights, or null when they were not staged. */
function embedModelDir() {
  const dir = path.join(resourcesDir(), "models", "bge-small-en-v1.5");
  return fs.existsSync(dir) ? dir : null;
}

/** KEY=VALUE config the Python side reads via ARGUS_ENV_FILE. */
function envFile() {
  return path.join(app.getPath("userData"), "config.env");
}

/**
 * In a dev checkout with no frozen exe staged we fall back to running the
 * backend from the repo venv, so the shell is usable before the first
 * PyInstaller build.
 */
function devBackendFallback() {
  const python = path.join(REPO_ROOT, ".venv", "Scripts", "python.exe");
  const script = path.join(REPO_ROOT, "desktop", "backend", "argus_server.py");
  return fs.existsSync(python) && fs.existsSync(script)
    ? { command: python, args: [script], cwd: REPO_ROOT }
    : null;
}

module.exports = {
  REPO_ROOT,
  resourcesDir,
  backendExe,
  nextServer,
  embedModelDir,
  envFile,
  devBackendFallback,
};
