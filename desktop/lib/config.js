"use strict";

const fs = require("node:fs");
const path = require("node:path");

/**
 * Read/write the KEY=VALUE file the Python side parses.
 *
 * The format has to match backend/config.py::parse_env_file exactly -- it is a
 * hand-rolled reader, not python-dotenv: no quoting, no escapes, no export
 * prefix, `#` comments, and everything after the first `=` is the value.
 */

function readEnvFile(file) {
  const values = {};
  if (!fs.existsSync(file)) return values;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    values[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return values;
}

function writeEnvFile(file, values) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const body = Object.entries(values)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  fs.writeFileSync(file, `${body}\n`, "utf8");
}

function updateEnvFile(file, patch) {
  const values = { ...readEnvFile(file), ...patch };
  writeEnvFile(file, values);
  return values;
}

/** True once a vault has been chosen -- i.e. onboarding can be skipped. */
function isConfigured(file) {
  const vault = readEnvFile(file).VAULT_PATH;
  return Boolean(vault && fs.existsSync(vault));
}

module.exports = { readEnvFile, writeEnvFile, updateEnvFile, isConfigured };
