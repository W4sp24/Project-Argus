"use strict";

const { autoUpdater } = require("electron-updater");
const log = require("electron-log");

/**
 * GitHub-Releases auto-update.
 *
 * autoDownload stays off on purpose: even with differential updates this can
 * be tens of megabytes, and silently pulling that on someone's tethered
 * connection is hostile. The renderer shows a toast and the user decides.
 *
 * Unsigned builds: electron-updater refuses to apply an update whose signature
 * it cannot verify, so electron-builder.yml sets verifyUpdateCodeSignature to
 * false. That is the trade for not having a code-signing certificate -- see
 * the README.
 */

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h
const FIRST_CHECK_DELAY_MS = 30 * 1000;

function initUpdater({ send, beforeInstall }) {
  autoUpdater.logger = log;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;

  autoUpdater.on("update-available", (info) =>
    send({ type: "available", version: info.version }),
  );
  autoUpdater.on("update-not-available", () => send({ type: "none" }));
  autoUpdater.on("download-progress", (p) =>
    send({
      type: "progress",
      percent: Math.round(p.percent),
      transferred: p.transferred,
      total: p.total,
    }),
  );
  autoUpdater.on("update-downloaded", (info) =>
    send({ type: "ready", version: info.version }),
  );
  autoUpdater.on("error", (error) => {
    log.error("updater:", error);
    send({ type: "error", message: String(error?.message ?? error) });
  });

  function check() {
    autoUpdater.checkForUpdates().catch((error) => log.warn("update check failed:", error));
  }

  setTimeout(check, FIRST_CHECK_DELAY_MS);
  setInterval(check, CHECK_INTERVAL_MS);

  return {
    check,
    download: () =>
      autoUpdater.downloadUpdate().catch((error) => log.error("download failed:", error)),
    /**
     * NSIS cannot overwrite files a running child still holds open -- the
     * Python bundle and Next's server.js among them -- so every child must be
     * fully reaped before we hand over. Failing to await this produces a
     * "cannot write file" mid-update and a half-installed app.
     */
    install: async () => {
      await beforeInstall();
      autoUpdater.quitAndInstall(false, true);
    },
  };
}

module.exports = { initUpdater };
