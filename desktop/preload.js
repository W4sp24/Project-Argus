"use strict";

const { contextBridge, ipcRenderer } = require("electron");

/**
 * Deliberately narrow surface: named operations only, no generic
 * ipcRenderer.invoke passthrough. The renderer loads a local page, but it also
 * renders agent output through react-markdown, so anything exposed here should
 * be assumed reachable by a prompt-injected response.
 *
 * The backend origin is injected because Next bakes its rewrites at build time
 * and cannot route to a port chosen at launch -- web/lib/api.ts reads this.
 */

const apiBase = process.argv.find((a) => a.startsWith("--argus-api="))?.slice(12) ?? "";
const wsBase = apiBase.replace(/^http/, "ws");

contextBridge.exposeInMainWorld("__ARGUS__", { apiBase, wsBase, platform: "win32" });

/** Wrap an ipcRenderer subscription so callers can actually unsubscribe. */
function subscribe(channel, callback) {
  const handler = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("argus", {
  // --- onboarding ---
  checkPrereqs: () => ipcRenderer.invoke("prereqs:check"),
  pickVault: () => ipcRenderer.invoke("vault:pick"),
  pickParentDir: () => ipcRenderer.invoke("vault:pick-parent"),
  useVault: (dir) => ipcRenderer.invoke("vault:use", dir),
  createVault: (parent, name) => ipcRenderer.invoke("vault:create", parent, name),
  initGit: (dir) => ipcRenderer.invoke("vault:init-git", dir),
  runDoctor: () => ipcRenderer.invoke("doctor:run"),
  finishOnboarding: () => ipcRenderer.send("onboarding:finish"),

  // --- shell ---
  openExternal: (url) => ipcRenderer.invoke("shell:open", url),
  appVersion: () => ipcRenderer.invoke("app:version"),

  // --- events ---
  onBootStage: (cb) => subscribe("boot:stage", cb),
  onUpdate: (cb) => subscribe("update:event", cb),
  downloadUpdate: () => ipcRenderer.send("update:download"),
  installUpdate: () => ipcRenderer.send("update:install"),
});
