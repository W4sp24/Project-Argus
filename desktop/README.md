# Argus Desktop

A Windows desktop shell around Argus: one installer, no Python, no Node, no
terminal. The Electron main process supervises the two things Argus already is —
a FastAPI backend and a Next.js dashboard — and shows the dashboard in a native
window.

The dev path is unchanged. `argus web`, `npm run dev`, and the Playwright suite
still use the fixed 8000/3000 ports and the Next rewrite; nothing here affects
them.

---

## What users still have to install

Two things genuinely cannot be bundled. The wizard **blocks** on both, on
purpose — permitting them as warnings just moves the failure to the middle of
someone's first chat turn, where it's far more confusing.

| Prerequisite | Why | Without it |
|---|---|---|
| **[Claude Code](https://claude.com/code)**, signed in | `claude-agent-sdk` spawns the Claude Code CLI and authenticates with the user's own Claude subscription (invariant I5). There is no API-key fallback. | Chat, the planner, study generation and the morning briefing are all dead |
| **[Git for Windows](https://git-scm.com/download/win)** | `backend/cli.py::_run_git` shells out to `git`; the writer snapshots the vault before every change (invariant I2) | Vault creation fails; no undo history |

Each user also needs their **own** Obsidian vault — Argus is single-user and
local-first. The wizard creates one from the bundled template if they don't
have one.

## Unsigned builds

There is no code-signing certificate, so:

- **On install**, Windows SmartScreen shows *"Windows protected your PC"*.
  Users must click **More info → Run anyway**. Tell them to expect it.
- `verifyUpdateCodeSignature: false` is set in `electron-builder.yml`, because
  electron-updater otherwise refuses to apply an update it can't verify.

An [Azure Trusted Signing](https://learn.microsoft.com/azure/trusted-signing/)
certificate (~$10/month) removes both. Set `win.certificateSubjectName` and drop
the `verifyUpdateCodeSignature` override when you have one.

---

## Development

```powershell
cd desktop
npm install
npm run icon          # build/icon.svg -> icon.ico + icon.png
npm run stage         # builds web/ with ARGUS_PACKAGED=1 and stages resources/
npm start             # launch the shell
```

`npm run stage` populates `resources/web`. Until you build the frozen backend,
the shell falls back to running the backend from the repo's `.venv`, so you can
work on the UI without a 20-minute PyInstaller cycle.

To build the frozen backend (needed for packaging, and to reproduce
freezing-only bugs):

```powershell
cd C:\path\to\Project-Argus
.\.venv\Scripts\python.exe -m pip install "pyinstaller==6.16.0" pyinstaller-hooks-contrib
.\.venv\Scripts\python.exe -m PyInstaller desktop\argus-backend.spec --noconfirm `
    --distpath desktop\resources --workpath desktop\build-py
```

Then always:

```powershell
.\.venv\Scripts\python.exe desktop\tests\smoke_backend.py `
    --target desktop\resources\backend\argus-backend.exe
```

**Run that smoke test after any dependency change.** Freezing breaks keyring,
apscheduler and chromadb *silently* — they resolve through entry points and
data files that PyInstaller can't see statically. The app still starts and
`/health` still returns 200; it dies later, in a packaged build, with no
console. The smoke test exercises the specific import chains that break.

### Packaging locally

```powershell
cd desktop
npm run pack          # unpacked, into dist/win-unpacked — fast
npm run dist          # full NSIS installer, into dist/
```

<details>
<summary><strong>"Cannot create symbolic link: A required privilege is not held by the client"</strong></summary>

`npm run pack` succeeds and `npm run dist` then fails during the NSIS step.

electron-builder unpacks a `winCodeSign` bundle that contains **macOS** OpenSSL
dylibs stored as symlinks. Creating a symlink on Windows requires Developer
Mode or an elevated shell, so the extraction fails — even though we're building
for Windows and signing nothing.

**Fix: turn on Settings → System → For developers → Developer Mode**, then
rerun. An elevated PowerShell works too.

Pre-extracting the cache by hand does *not* help: electron-builder downloads to
a freshly randomised `<hash>.7z` on every invocation, so there is nothing
stable to prepare.

CI is unaffected — `windows-latest` runners already hold the privilege.
</details>

## Cutting a release

```powershell
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/release.yml` builds on `windows-latest` and publishes the
installer, its `.blockmap` and `latest.yml` to a GitHub Release. Installed apps
check 30 s after launch and every 6 h after that.

Bump `desktop/package.json`'s version to match the tag, or let CI do it — the
workflow runs `npm version` from `GITHUB_REF_NAME`.

**Rehearse the update loop before the first real release.** Publish
`v0.0.1-test` as a pre-release, install it, then publish `v0.0.2-test` with a
UI-only change and confirm the toast appears, the download is a small fraction
of the full installer, and the app relaunches with the vault intact.

---

## How it works

```
app.whenReady
  → requestSingleInstanceLock          two instances = two ~700MB Python processes
  → read %APPDATA%/Argus/config.env
  → no VAULT_PATH?  → onboarding wizard (native file:// page)
  → splash window
  → spawn argus-backend.exe            prints {"port": N} once its socket is bound
  → poll /health
  → spawn Next standalone              via ELECTRON_RUN_AS_NODE on a free port
  → poll /dashboard
  → main window, splash closes
```

### Why the backend port is announced, not assigned

`argus-backend.exe` binds a socket to port 0 **itself** and only then prints the
port. Probing for a free port in the parent and passing it down leaves a window
where something else can take it.

### Why the API base URL is injected at runtime

Next serializes `rewrites()` into `.next/routes-manifest.json` at **build**
time; `next start` and the standalone server never re-read `next.config.mjs`.
A port chosen at launch therefore cannot travel through a rewrite. Instead
`preload.js` injects `window.__ARGUS__ = { apiBase, wsBase }` and
`web/lib/api.ts` resolves URLs against it.

**Always use `apiFetch` from `web/lib/api.ts`.** A bare `fetch("/api/...")`
works in dev — the rewrite catches it — and silently 404s in the packaged app.

### Why children die on their own

Three layers, because none of them covers every case on Windows:

1. **Each child watches our PID and exits when we go.** The only layer that
   survives Task Manager "End task" or `taskkill /F` on Electron, which run no
   handlers here.
2. **`before-quit` → `taskkill /T /F`**, awaited. Also required for updates:
   NSIS can't overwrite `torch_cpu.dll` or `server.js` while a child holds them
   open.
3. **`will-quit` → synchronous sweep**, last resort.

A Windows Job Object would be strictly stronger than 1–3, but every npm binding
is an unmaintained native addon needing per-Electron-ABI rebuilds. Layer 1
covers the same failure with no native code.

> **The backend's watchdog must never read stdin.** A thread blocked in a stdin
> read stalls C-extension imports process-wide: `import numpy` never completes,
> so `/api/doctor` and `/api/search` hang forever while `/health` still answers
> 200. It waits on the parent's process handle instead. Measured 0.13 s vs
> >20 s — see the comment in `backend/argus_server.py` and the
> `parent-death watchdog` check in `tests/smoke_backend.py`.

### Security

`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, and a
`BrowserWindow` pointed at loopback — not a `<webview>`, which would add a guest
process and complicate the IPC path for no benefit on first-party content.

The preload exposes named operations only, no generic `invoke` passthrough. The
CSP in `web/next.config.mjs` pins `connect-src` to loopback, which matters
because the chat surface renders agent output through `react-markdown`: a
prompt-injected response must not be able to reach a remote origin with vault
content. `obsidian:` is allowed through `shell.openExternal` because
`backend/journal.py` returns `obsidian_uri` deep links.

### Sizes

| | |
|---|---|
| Next standalone | ~18 MB |
| Frozen backend | ~800 MB (torch is ~400 MB of it) |
| Installed | ~1.2 GB |
| NSIS installer | ~550–700 MB |
| Typical UI-only update | 10–40 MB (differential) |

Differential updates work well because the Python bundle sits in
`extraResources`, outside `app.asar` — `torch_cpu.dll` and the model weights are
byte-identical between releases, so their blocks hash-match and aren't
re-downloaded. A Python dependency bump invalidates ~400 MB of that. If
differential ever fails, electron-updater falls back to a full download.

**The obvious size win** is dropping torch: `onnxruntime` is *already* in the
tree as a chromadb dependency, so exporting bge-small to ONNX and running
embeddings through it deletes torch, transformers, scikit-learn and scipy —
roughly −250 MB on the installer, and it removes the three nastiest PyInstaller
failure modes. It costs ~40 lines in `backend/rag/index.py::_embed` (whose lazy
import is already the seam) and forces a one-time reindex.

## Layout

```
desktop/
  main.js                  lifecycle, ports, spawn, health gate, guards
  preload.js               contextBridge: __ARGUS__ + argus.*
  lib/
    paths.js               packaged vs dev resource resolution
    config.js              read/write config.env (matches parse_env_file)
    children.js            spawn, handshake, layered reaping
    next-bootstrap.js      parent-death guard, copied next to server.js
    prereqs.js             Claude Code + git detection
    updater.js             electron-updater wiring
  onboarding/              native first-run wizard
  splash/                  boot progress
  backend/argus_server.py  PyInstaller entry (--init / --doctor / serve)
  argus-backend.spec       freezing config — read the comments before editing
  tests/smoke_backend.py   Tier-1 gate, also run in CI
  scripts/                 make-icon.mjs, stage.mjs
```
