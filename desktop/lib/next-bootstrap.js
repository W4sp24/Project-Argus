"use strict";

/**
 * Wrapper around Next's standalone server.js, run by Electron's own Node via
 * ELECTRON_RUN_AS_NODE=1 (so end users need no Node install).
 *
 * It exists for one reason: to make the Next process die with its parent. If
 * Electron is force-killed, no handler in the main process runs, and an
 * orphaned server would keep the port -- and keep server.js locked, which
 * blocks the NSIS updater from replacing it.
 *
 * Polling is fine here: this process holds no GIL-equivalent and a 2s tick is
 * free. (The Python side needs a blocking wait instead -- see
 * desktop/backend/argus_server.py.)
 *
 * This file is copied next to server.js by scripts/stage.mjs so it lives
 * outside app.asar, where a plain Node process can read it.
 */

const parentPid = Number(process.env.ARGUS_PARENT_PID || 0);

if (parentPid > 0) {
  const timer = setInterval(() => {
    try {
      process.kill(parentPid, 0); // signal 0 = existence probe, sends nothing
    } catch {
      process.exit(0);
    }
  }, 2000);
  timer.unref(); // never hold the event loop open on our account
}

require("./server.js");
