"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { isNotebookUrl } = require("../lib/windows");

const ORIGIN = "http://127.0.0.1:41234";

test("allows the notebook route on the app's own origin", () => {
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook`, ORIGIN), true);
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook?window=standalone`, ORIGIN), true);
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook/flashcards/3/review`, ORIGIN), true);
});

test("refuses any other route, so deny-by-default survives", () => {
  assert.equal(isNotebookUrl(`${ORIGIN}/dashboard`, ORIGIN), false);
  assert.equal(isNotebookUrl(`${ORIGIN}/`, ORIGIN), false);
  assert.equal(isNotebookUrl(`${ORIGIN}/system`, ORIGIN), false);
});

test("refuses another origin wearing the notebook path", () => {
  assert.equal(isNotebookUrl("https://evil.example/notebook", ORIGIN), false);
  // A different port is a different origin, and the Next server's port is
  // chosen at launch -- so this is not hypothetical.
  assert.equal(isNotebookUrl("http://127.0.0.1:9999/notebook", ORIGIN), false);
});

test("refuses a userinfo trick that only looks like the right origin", () => {
  // The authority here is evil.example; 127.0.0.1:41234 is a username. A
  // prefix match on the string would have allowed it.
  assert.equal(isNotebookUrl("http://127.0.0.1:41234@evil.example/notebook", ORIGIN), false);
});

test("refuses a path that merely starts with the same letters", () => {
  assert.equal(isNotebookUrl(`${ORIGIN}/notebookery`, ORIGIN), false);
  assert.equal(isNotebookUrl(`${ORIGIN}/notebook-admin`, ORIGIN), false);
});

test("refuses a non-http scheme", () => {
  assert.equal(isNotebookUrl("file:///C:/notebook", ORIGIN), false);
  assert.equal(isNotebookUrl("javascript:alert(1)//notebook", ORIGIN), false);
});

test("refuses junk rather than throwing", () => {
  assert.equal(isNotebookUrl("not a url", ORIGIN), false);
  assert.equal(isNotebookUrl("", ORIGIN), false);
  assert.equal(isNotebookUrl(undefined, ORIGIN), false);
});

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { readBounds, writeBounds } = require("../lib/windows");

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "argus-win-")), "window-state.json");
}

test("bounds round-trip", () => {
  const file = tmpFile();
  writeBounds(file, { x: 10, y: 20, width: 1280, height: 880 });
  assert.deepEqual(readBounds(file), { width: 1280, height: 880, x: 10, y: 20 });
});

test("a missing or unreadable file opens at the default size", () => {
  assert.deepEqual(readBounds(path.join(os.tmpdir(), "argus-nope", "nothing.json")), {});
  const file = tmpFile();
  fs.writeFileSync(file, "{ not json", "utf8");
  assert.deepEqual(readBounds(file), {});
});

test("a size with no position still restores the size", () => {
  // A saved position from a monitor that is no longer attached would open the
  // window off-screen, so position is dropped unless both halves are usable.
  const file = tmpFile();
  writeBounds(file, { width: 1000, height: 700 });
  assert.deepEqual(readBounds(file), { width: 1000, height: 700 });
});

test("nonsense dimensions are refused rather than passed to the window", () => {
  const file = tmpFile();
  writeBounds(file, { width: null, height: "big" });
  assert.deepEqual(readBounds(file), {});
});

const { notebookWindowOptions } = require("../lib/windows");

test("the notebook window carries the preload and the backend port", () => {
  // These two fail ONLY in a packaged build: dev is same-origin through the
  // Next rewrite, so a window missing them works fine until it ships.
  const options = notebookWindowOptions({
    dirname: "C:\app",
    apiOrigin: "http://127.0.0.1:41234",
  });
  assert.equal(options.webPreferences.preload, path.join("C:\app", "preload.js"));
  assert.deepEqual(options.webPreferences.additionalArguments, [
    "--argus-api=http://127.0.0.1:41234",
  ]);
});

test("the notebook window keeps the shell's security posture", () => {
  const { webPreferences } = notebookWindowOptions({ dirname: ".", apiOrigin: "http://x" });
  assert.equal(webPreferences.contextIsolation, true);
  assert.equal(webPreferences.sandbox, true);
  assert.equal(webPreferences.nodeIntegration, false);
  assert.equal(webPreferences.webSecurity, true);
});

test("remembered bounds override the defaults but never the webPreferences", () => {
  const options = notebookWindowOptions({
    dirname: ".",
    apiOrigin: "http://x",
    bounds: { width: 1000, height: 700, x: 5, y: 6 },
  });
  assert.equal(options.width, 1000);
  assert.equal(options.x, 5);
  assert.equal(options.webPreferences.sandbox, true);
});
