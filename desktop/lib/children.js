"use strict";

const net = require("node:net");
const { spawn, execFileSync } = require("node:child_process");
const log = require("electron-log");

/**
 * Child-process supervision for the backend and the Next server.
 *
 * Cleanup is layered because no single mechanism covers every way Electron can
 * die on Windows:
 *
 *   1. Each child watches our PID itself and exits when we go
 *      (--parent-pid for the backend, a tiny bootstrap for Next). This is the
 *      ONLY layer that survives Task Manager "End task" / taskkill /F on us,
 *      because those run no handlers here.
 *   2. before-quit -> taskkill /T /F per child, awaited. `/T` matters: it takes
 *      the whole tree.
 *   3. will-quit -> synchronous taskkill as a last resort.
 *
 * Layer 2 is also load-bearing for updates: NSIS cannot overwrite
 * torch_cpu.dll or server.js while a child still holds them open, so the
 * children must be fully reaped before quitAndInstall proceeds.
 */

const children = new Map(); // name -> { proc, tail: string[] }
const TAIL_LINES = 60;

/** Ask the OS for a free loopback port. */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function record(name, stream, tail) {
  let buffer = "";
  stream.on("data", (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      tail.push(line);
      if (tail.length > TAIL_LINES) tail.shift();
      log.info(`[${name}] ${line}`);
    }
  });
}

/**
 * Spawn a supervised child. `onFirstLine` receives stdout's first line, which
 * is how the backend hands us its port.
 */
function start(name, command, args, options = {}) {
  const proc = spawn(command, args, {
    cwd: options.cwd,
    env: { ...process.env, ...options.env },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const entry = { proc, tail: [], name };
  children.set(name, entry);

  record(name, proc.stdout, entry.tail);
  record(name, proc.stderr, entry.tail);

  proc.on("exit", (code, signal) => {
    log.info(`[${name}] exited code=${code} signal=${signal}`);
    entry.exited = true;
    if (options.onExit) options.onExit(code, signal, entry.tail.join("\n"));
  });
  proc.on("error", (error) => log.error(`[${name}] spawn failed: ${error.message}`));

  return entry;
}

/** Resolve with the first stdout line parsed as JSON, or reject on timeout. */
function firstJsonLine(entry, timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`${entry.name}: no handshake within ${timeoutMs}ms\n${entry.tail.join("\n")}`));
    }, timeoutMs);

    function onData(chunk) {
      buffer += chunk.toString();
      const newline = buffer.indexOf("\n");
      if (newline === -1) return;
      const line = buffer.slice(0, newline).trim();
      try {
        const payload = JSON.parse(line);
        cleanup();
        resolve(payload);
      } catch {
        buffer = buffer.slice(newline + 1); // pre-handshake noise; keep waiting
      }
    }
    function onExit() {
      cleanup();
      reject(new Error(`${entry.name}: exited before handshake\n${entry.tail.join("\n")}`));
    }
    function cleanup() {
      clearTimeout(timer);
      entry.proc.stdout.off("data", onData);
      entry.proc.off("exit", onExit);
    }

    entry.proc.stdout.on("data", onData);
    entry.proc.on("exit", onExit);
  });
}

function killTreeSync(pid) {
  try {
    execFileSync("taskkill", ["/T", "/F", "/PID", String(pid)], {
      windowsHide: true,
      stdio: "ignore",
    });
  } catch {
    // already gone
  }
}

/** Reap every child, awaiting exit. Resolves once nothing is left running. */
async function stopAll(timeoutMs = 5000) {
  const pending = [];
  for (const entry of children.values()) {
    if (entry.exited || entry.proc.exitCode !== null) continue;
    pending.push(
      new Promise((resolve) => {
        const done = setTimeout(resolve, timeoutMs);
        entry.proc.once("exit", () => {
          clearTimeout(done);
          resolve();
        });
        killTreeSync(entry.proc.pid);
      }),
    );
  }
  await Promise.all(pending);
  children.clear();
}

/** Last-resort synchronous sweep for will-quit / process exit. */
function stopAllSync() {
  for (const entry of children.values()) {
    if (!entry.exited && entry.proc.exitCode === null) killTreeSync(entry.proc.pid);
  }
  children.clear();
}

function tailOf(name) {
  return children.get(name)?.tail.join("\n") ?? "";
}

module.exports = { freePort, start, firstJsonLine, stopAll, stopAllSync, tailOf };
