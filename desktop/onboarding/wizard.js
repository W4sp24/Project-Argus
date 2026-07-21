"use strict";

/**
 * First-run wizard. Runs as a local file:// page *before* the backend starts,
 * because backend/config.py raises ConfigError until VAULT_PATH exists -- a
 * dashboard-hosted wizard would be a UI whose only working endpoint is
 * /health, served by a process that cannot function, after a 15s torch import.
 */

const STEPS = ["welcome", "prereqs", "vault", "check"];
const state = { vault: null, prereqsOk: false };

function show(step) {
  for (const section of document.querySelectorAll("section")) {
    section.classList.toggle("on", section.dataset.panel === step);
  }
  const index = STEPS.indexOf(step);
  document.querySelectorAll(".steps li").forEach((li, i) => {
    li.classList.toggle("on", i === index);
    li.classList.toggle("done", i < index);
  });
  if (step === "prereqs") checkPrereqs();
  if (step === "check") runDoctor();
}

function row({ cls, badge, name, detail, url }) {
  const link = url
    ? `<a href="#" data-url="${url}">Get it →</a>`
    : "";
  return `<div class="row ${cls}">
    <span class="badge">${badge}</span>
    <span class="body">
      <span class="name">${name}</span>
      <div class="detail">${detail}</div>
      ${link}
    </span>
  </div>`;
}

function wireLinks(container) {
  for (const anchor of container.querySelectorAll("a[data-url]")) {
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      window.argus.openExternal(anchor.dataset.url);
    });
  }
}

// --- step 2: prerequisites -------------------------------------------------

async function checkPrereqs() {
  const list = document.getElementById("prereq-list");
  const next = document.getElementById("prereq-next");
  list.innerHTML = `<p class="muted">Checking…</p>`;
  next.disabled = true;

  const checks = await window.argus.checkPrereqs();
  list.innerHTML = checks
    .map((check) =>
      row({
        cls: check.ok ? "ok" : "fail",
        badge: check.ok ? "FOUND" : "MISSING",
        name: check.label,
        detail: `${check.detail}<br>${check.help}`,
        url: check.ok ? null : check.url,
      }),
    )
    .join("");
  wireLinks(list);

  // Hard block, not a warning: without these the app installs and then fails
  // in the middle of the first chat turn, which is far more confusing.
  state.prereqsOk = checks.every((check) => check.ok);
  next.disabled = !state.prereqsOk;
}

// --- step 3: vault ---------------------------------------------------------

function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function syncPanes() {
  const isNew = currentMode() === "new";
  document.getElementById("pane-new").classList.toggle("hidden", !isNew);
  document.getElementById("pane-existing").classList.toggle("hidden", isNew);
  document.getElementById("vault-error").textContent = "";
}

async function chooseVault() {
  const error = document.getElementById("vault-error");
  const button = document.getElementById("vault-next");
  error.textContent = "";
  button.disabled = true;

  try {
    if (currentMode() === "existing") {
      const dir = document.getElementById("existing-path").value;
      if (!dir) throw new Error("Choose your vault folder first.");
      const result = await window.argus.useVault(dir);
      if (!result.ok) throw new Error(result.error);
      state.vault = result.vault;

      // Not a git repo means no pre-apply snapshots, so doctor will FAIL and
      // Argus loses its undo story. Offer to fix it rather than just warn.
      if (!result.git) {
        const init = await window.argus.initGit(result.vault);
        if (!init.ok) {
          error.textContent = `Vault set, but git init failed: ${init.error}`;
        }
      }
    } else {
      const parent = document.getElementById("parent-path").value;
      const name = document.getElementById("vault-name").value;
      if (!parent) throw new Error("Choose where the vault should go.");
      if (!name.trim()) throw new Error("Give the vault folder a name.");
      const result = await window.argus.createVault(parent, name);
      if (!result.ok) throw new Error(result.error);
      state.vault = result.vault;
    }
    show("check");
  } catch (exception) {
    error.textContent = exception.message;
  } finally {
    button.disabled = false;
  }
}

// --- step 4: doctor --------------------------------------------------------

async function runDoctor() {
  const list = document.getElementById("doctor-list");
  const error = document.getElementById("check-error");
  const finish = document.getElementById("finish");
  list.innerHTML = `<p class="muted">Running…</p>`;
  error.textContent = "";
  finish.disabled = true;

  const result = await window.argus.runDoctor();
  if (!result.ok) {
    list.innerHTML = "";
    error.textContent = result.error || "Health check failed.";
    return;
  }

  list.innerHTML = result.checks
    .map((check) =>
      row({
        cls: check.status.toLowerCase(),
        badge: check.status,
        name: check.name,
        detail: check.detail,
      }),
    )
    .join("");

  // WARN rows (gcal/todoist not connected) are expected on a fresh install and
  // must not block; only a hard FAIL does.
  const blocked = result.checks.some((check) => check.status === "FAIL");
  finish.disabled = blocked;
  if (blocked) error.textContent = "Fix the failing checks above, then run again.";
}

// --- wiring ----------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  for (const button of document.querySelectorAll("[data-go]")) {
    button.addEventListener("click", () => show(button.dataset.go));
  }
  document.getElementById("recheck").addEventListener("click", checkPrereqs);
  document.getElementById("rerun").addEventListener("click", runDoctor);
  document.getElementById("vault-next").addEventListener("click", chooseVault);
  document.getElementById("finish").addEventListener("click", () =>
    window.argus.finishOnboarding(),
  );

  for (const radio of document.querySelectorAll('input[name="mode"]')) {
    radio.addEventListener("change", syncPanes);
  }

  document.getElementById("browse-existing").addEventListener("click", async () => {
    const dir = await window.argus.pickVault();
    if (!dir) return;
    document.getElementById("existing-path").value = dir;
    document.getElementById("existing-note").textContent = "";
  });
  document.getElementById("browse-parent").addEventListener("click", async () => {
    const dir = await window.argus.pickParentDir();
    if (dir) document.getElementById("parent-path").value = dir;
  });

  show("welcome");
});
