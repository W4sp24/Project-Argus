"use strict";

const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");
const { execFile } = require("node:child_process");
const { app, BrowserWindow, dialog, ipcMain, shell, session } = require("electron");
const log = require("electron-log");

const paths = require("./lib/paths");
const conf = require("./lib/config");
const prereqs = require("./lib/prereqs");
const children = require("./lib/children");
const { initUpdater } = require("./lib/updater");

log.transports.file.level = "info";
log.info(`Argus desktop ${app.getVersion()} starting`);

let splashWindow = null;
let onboardingWindow = null;
let mainWindow = null;
let updater = null;
let backendPort = null;
let nextPort = null;
let quitting = false;

// --- helpers ---------------------------------------------------------------

function stage(stageName, detail) {
  log.info(`boot: ${stageName}${detail ? ` - ${detail}` : ""}`);
  for (const win of [splashWindow, onboardingWindow]) {
    if (win && !win.isDestroyed()) win.webContents.send("boot:stage", { stage: stageName, detail });
  }
}

function get(url, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode);
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(0);
    });
    request.on("error", () => resolve(0));
  });
}

/** Poll until `url` answers 200, or give up. */
async function waitForHttp(url, { timeoutMs = 60000, intervalMs = 400 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await get(url)) return true;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

function fatal(title, detail) {
  log.error(`${title}: ${detail}`);
  dialog.showErrorBox(title, detail);
  quitting = true;
  children.stopAllSync();
  app.exit(1);
}

// --- child processes -------------------------------------------------------

async function startBackend() {
  stage("backend", "starting Argus services");
  // Reserved before the backend starts because the backend needs to allow this
  // exact origin through CORS.
  if (nextPort === null) nextPort = await children.freePort();

  const exe = paths.backendExe();
  let command;
  let args;
  let cwd;
  if (fs.existsSync(exe)) {
    command = exe;
    args = [];
    cwd = path.dirname(exe);
  } else {
    // Dev checkout with nothing staged yet: fall back to the repo venv.
    const fallback = paths.devBackendFallback();
    if (!fallback) {
      fatal(
        "Argus backend missing",
        `Expected ${exe}.\n\nRun \`npm run stage\` in desktop/ (dev) or reinstall Argus.`,
      );
      return false;
    }
    log.warn("frozen backend not found; using dev venv fallback");
    ({ command, args, cwd } = fallback);
  }

  const modelDir = paths.embedModelDir();
  const entry = children.start("backend", command, [...args, "--parent-pid", String(process.pid)], {
    cwd,
    env: {
      ARGUS_ENV_FILE: paths.envFile(),
      // The dashboard is served from a port picked at launch, so it is a
      // cross-origin caller here (unlike dev, where the Next rewrite makes
      // everything same-origin). Without this every API call fails CORS and
      // the UI loads but shows "couldn't reach the backend" everywhere.
      ARGUS_ALLOWED_ORIGINS: [
        `http://127.0.0.1:${nextPort}`,
        `http://localhost:${nextPort}`,
      ].join(","),
      // Pre-baked weights so a fresh install never depends on a HuggingFace
      // fetch that can rate-limit or be blocked.
      ...(modelDir
        ? { ARGUS_EMBED_MODEL: modelDir, HF_HUB_OFFLINE: "1", TRANSFORMERS_OFFLINE: "1" }
        : {}),
    },
    onExit: (code, _signal, tail) => {
      if (quitting) return;
      fatal("Argus backend stopped", `Exit code ${code}.\n\n${tail.slice(-2000)}`);
    },
  });

  try {
    const handshake = await children.firstJsonLine(entry, 120000);
    backendPort = handshake.port;
  } catch (error) {
    fatal("Argus backend failed to start", String(error.message));
    return false;
  }

  stage("backend", "waiting for health check");
  if (!(await waitForHttp(`http://127.0.0.1:${backendPort}/health`, { timeoutMs: 60000 }))) {
    fatal("Argus backend not responding", children.tailOf("backend").slice(-2000));
    return false;
  }
  log.info(`backend ready on ${backendPort}`);
  return true;
}

async function startNext() {
  stage("ui", "starting the dashboard");

  const server = paths.nextServer();
  if (!fs.existsSync(server)) {
    fatal(
      "Argus dashboard missing",
      `Expected ${server}.\n\nRun \`npm run stage\` in desktop/ (dev) or reinstall Argus.`,
    );
    return false;
  }

  const bootstrap = path.join(path.dirname(server), "argus-bootstrap.js");
  const entryScript = fs.existsSync(bootstrap) ? bootstrap : server;

  children.start("next", process.execPath, [entryScript], {
    cwd: path.dirname(server),
    env: {
      // Runs Electron's embedded Node instead of booting Chromium.
      ELECTRON_RUN_AS_NODE: "1",
      NODE_ENV: "production",
      PORT: String(nextPort),
      // Without an explicit HOSTNAME the standalone server binds 0.0.0.0 and
      // Windows Firewall prompts on first launch.
      HOSTNAME: "127.0.0.1",
      ARGUS_PARENT_PID: String(process.pid),
    },
    onExit: (code, _signal, tail) => {
      if (quitting) return;
      fatal("Argus dashboard stopped", `Exit code ${code}.\n\n${tail.slice(-2000)}`);
    },
  });

  if (!(await waitForHttp(`http://127.0.0.1:${nextPort}/dashboard`, { timeoutMs: 60000 }))) {
    fatal("Argus dashboard not responding", children.tailOf("next").slice(-2000));
    return false;
  }
  log.info(`next ready on ${nextPort}`);
  return true;
}

// --- windows ---------------------------------------------------------------

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 260,
    frame: false,
    resizable: false,
    show: true,
    backgroundColor: "#06040c",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  splashWindow.loadFile(path.join(__dirname, "splash", "index.html"));
}

function createOnboarding() {
  return new Promise((resolve) => {
    onboardingWindow = new BrowserWindow({
      width: 720,
      height: 620,
      resizable: false,
      show: false,
      backgroundColor: "#06040c",
      title: "Welcome to Argus",
      icon: path.join(__dirname, "build", "icon.ico"),
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: path.join(__dirname, "preload.js"),
      },
    });
    onboardingWindow.setMenuBarVisibility(false);
    onboardingWindow.loadFile(path.join(__dirname, "onboarding", "index.html"));
    onboardingWindow.once("ready-to-show", () => onboardingWindow.show());

    ipcMain.once("onboarding:finish", () => {
      if (onboardingWindow && !onboardingWindow.isDestroyed()) onboardingWindow.close();
      onboardingWindow = null;
      resolve(true);
    });
    onboardingWindow.on("closed", () => {
      // Closed via the X without finishing -> nothing is configured, so quit
      // rather than drop the user into a dashboard that 503s on every call.
      if (onboardingWindow !== null) {
        onboardingWindow = null;
        resolve(false);
      }
    });
  });
}

function createMainWindow() {
  const origin = `http://127.0.0.1:${nextPort}`;
  const apiOrigin = `http://127.0.0.1:${backendPort}`;

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: "#06040c",
    title: "Argus",
    icon: path.join(__dirname, "build", "icon.ico"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      preload: path.join(__dirname, "preload.js"),
      additionalArguments: [`--argus-api=${apiOrigin}`],
    },
  });
  mainWindow.setMenuBarVisibility(false);

  // Never open a second BrowserWindow; hand real links to the OS browser.
  // obsidian: is load-bearing -- backend/journal.py returns obsidian_uri deep
  // links that the UI renders as "open in Obsidian".
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith(origin)) return;
    event.preventDefault();
    if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
    splashWindow = null;
  });

  mainWindow.webContents.on("did-fail-load", (_e, code, description, url) => {
    if (code === -3) return; // aborted, usually a redirect
    log.error(`load failed ${code} ${description} ${url}`);
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.loadURL(`${origin}/dashboard`);
}

// --- IPC -------------------------------------------------------------------

function runBackendCommand(args, timeoutMs = 120000) {
  return new Promise((resolve) => {
    const exe = paths.backendExe();
    let command = exe;
    let fullArgs = args;
    let cwd = path.dirname(exe);
    if (!fs.existsSync(exe)) {
      const fallback = paths.devBackendFallback();
      if (!fallback) return resolve({ ok: false, error: "backend executable not found" });
      command = fallback.command;
      fullArgs = [...fallback.args, ...args];
      cwd = fallback.cwd;
    }
    execFile(
      command,
      fullArgs,
      {
        cwd,
        windowsHide: true,
        timeout: timeoutMs,
        env: { ...process.env, ARGUS_ENV_FILE: paths.envFile() },
      },
      (error, stdout, stderr) => {
        const line = String(stdout || "").trim().split(/\r?\n/).filter(Boolean).pop();
        try {
          return resolve(JSON.parse(line));
        } catch {
          return resolve({
            ok: false,
            error: String(stderr || error?.message || "backend command failed").slice(-1200),
          });
        }
      },
    );
  });
}

function registerIpc() {
  ipcMain.handle("prereqs:check", () => prereqs.checkAll());
  ipcMain.handle("app:version", () => app.getVersion());

  ipcMain.handle("shell:open", (_event, url) => {
    if (typeof url !== "string" || !/^(https:|obsidian:)/i.test(url)) return false;
    shell.openExternal(url);
    return true;
  });

  ipcMain.handle("vault:pick", async () => {
    const result = await dialog.showOpenDialog(onboardingWindow ?? undefined, {
      title: "Choose your Obsidian vault",
      properties: ["openDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle("vault:pick-parent", async () => {
    const result = await dialog.showOpenDialog(onboardingWindow ?? undefined, {
      title: "Where should the new vault go?",
      properties: ["openDirectory", "createDirectory"],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle("vault:use", (_event, dir) => {
    if (typeof dir !== "string" || !path.isAbsolute(dir) || !fs.existsSync(dir)) {
      return { ok: false, error: "Pick an existing folder." };
    }
    conf.updateEnvFile(paths.envFile(), { VAULT_PATH: path.resolve(dir) });
    return { ok: true, vault: path.resolve(dir), git: fs.existsSync(path.join(dir, ".git")) };
  });

  ipcMain.handle("vault:create", async (_event, parent, name) => {
    if (typeof parent !== "string" || !path.isAbsolute(parent) || !fs.existsSync(parent)) {
      return { ok: false, error: "Pick an existing parent folder." };
    }
    const safe = String(name || "").trim();
    // Keep the name a single path segment: no separators, no traversal.
    if (!safe || /[\\/:*?"<>|]/.test(safe) || safe === "." || safe === "..") {
      return { ok: false, error: "Use a simple folder name, without slashes." };
    }
    return runBackendCommand(["--init", path.join(parent, safe)]);
  });

  ipcMain.handle("vault:init-git", async (_event, dir) => {
    if (typeof dir !== "string" || !path.isAbsolute(dir) || !fs.existsSync(dir)) {
      return { ok: false, error: "Folder not found." };
    }
    const git = (args) =>
      new Promise((resolve) => {
        execFile("git", args, { cwd: dir, windowsHide: true }, (error, stdout, stderr) =>
          resolve({ ok: !error, out: String(stdout || ""), err: String(stderr || error?.message || "") }),
        );
      });

    const init = await git(["init"]);
    if (!init.ok) return { ok: false, error: init.err };

    // Same trap as backend/cli.py::_ensure_git_identity: `git commit` fails
    // outright when the machine has no user.name/user.email, which is the
    // default for anyone who installed Git for Windows and never configured
    // it. Set it repo-locally only when git cannot resolve one already.
    if (!(await git(["var", "GIT_AUTHOR_IDENT"])).ok) {
      await git(["config", "user.name", "Argus"]);
      await git(["config", "user.email", "argus@localhost"]);
    }

    await git(["add", "-A"]);
    const commit = await git(["commit", "-m", "chore: initial snapshot for Argus"]);
    if (!commit.ok) return { ok: false, error: commit.err };
    return { ok: true };
  });

  ipcMain.handle("doctor:run", () => runBackendCommand(["--doctor"]));

  ipcMain.on("update:download", () => updater?.download());
  ipcMain.on("update:install", () => updater?.install());
}

// --- lifecycle -------------------------------------------------------------

if (!app.requestSingleInstanceLock()) {
  // Two instances would mean two ~700MB Python processes fighting over one vault.
  app.exit(0);
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      callback(permission === "clipboard-read");
    });
    app.on("web-contents-created", (_event, contents) => {
      contents.on("will-attach-webview", (event) => event.preventDefault());
    });

    registerIpc();

    if (!conf.isConfigured(paths.envFile())) {
      log.info("no vault configured; showing onboarding");
      const finished = await createOnboarding();
      if (!finished) {
        quitting = true;
        app.exit(0);
        return;
      }
    }

    createSplash();
    if (!(await startBackend())) return;
    if (!(await startNext())) return;

    updater = initUpdater({
      send: (payload) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("update:event", payload);
        }
      },
      beforeInstall: async () => {
        quitting = true;
        await children.stopAll();
      },
    });

    createMainWindow();
  });

  app.on("window-all-closed", () => {
    quitting = true;
    app.quit();
  });

  let reaping = false;
  app.on("before-quit", (event) => {
    quitting = true;
    if (reaping) return;
    reaping = true;
    event.preventDefault();
    children.stopAll(5000).finally(() => app.exit(0));
  });

  app.on("will-quit", () => children.stopAllSync());
  process.on("exit", () => children.stopAllSync());
}
