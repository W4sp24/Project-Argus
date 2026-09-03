"use strict";

const fs = require("node:fs");
const path = require("node:path");

/**
 * The one exception to "deny every popup".
 *
 * `main.js` denied every `window.open` by policy, and that policy is worth
 * keeping: the renderer displays agent output through react-markdown, so
 * anything a prompt-injected response can reach must be assumed reachable by
 * an attacker. The Notebook needs a real second window, so this narrows the
 * exception to a single origin and a single route rather than relaxing the
 * handler.
 *
 * Parsed with `URL` rather than matched as a prefix, which is what makes the
 * narrow cases fall the right way:
 *
 *   - `/notebookery` is not the notebook.
 *   - `http://127.0.0.1:41234@evil.example/notebook` has authority
 *     `evil.example`; the part that looks like the app is a username.
 *   - A different port is a different origin, and the Next server's port is
 *     chosen at launch, so that is not a hypothetical case.
 *   - `file:` and `javascript:` have no matching origin at all.
 */
function isNotebookUrl(url, origin) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.origin !== origin) return false;
  return parsed.pathname === "/notebook" || parsed.pathname.startsWith("/notebook/");
}

/**
 * Where the Notebook window was last left.
 *
 * Deliberately *not* in `config.env`: that file is a strict KEY=VALUE contract
 * parsed by `backend/core/config.py::parse_env_file`, and window geometry is
 * not the backend's business. This is a sibling JSON file in the same
 * userData directory instead.
 *
 * The file path is a parameter rather than derived from `app.getPath` so this
 * module needs no `electron` import and stays runnable under `node --test`.
 */
function readBounds(file) {
  try {
    const saved = JSON.parse(fs.readFileSync(file, "utf8"));
    const { x, y, width, height } = saved ?? {};
    // Width and height alone are enough to restore usefully, and a saved
    // position from a monitor that is no longer attached would otherwise open
    // the window off-screen.
    if (!Number.isFinite(width) || !Number.isFinite(height)) return {};
    const bounds = { width, height };
    if (Number.isFinite(x) && Number.isFinite(y)) Object.assign(bounds, { x, y });
    return bounds;
  } catch {
    // Missing, unreadable, or hand-edited into nonsense: open at the default
    // size rather than fail to open at all.
    return {};
  }
}

function writeBounds(file, bounds) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(bounds), "utf8");
  } catch {
    // Best-effort. Losing the remembered size is not worth an error dialog.
  }
}

module.exports = { isNotebookUrl, readBounds, writeBounds };
