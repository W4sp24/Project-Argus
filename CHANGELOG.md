# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Argus is currently pre-1.0 (0.x releases).

## [0.2.1]

Bug-fix release for v0.2.0. Two distinct classes of failure: a build that
shipped without dependencies it imports, and features whose UI was never
connected to a backend.

> **v0.2.0 published as an empty release and is permanently unrecoverable.**
> GitHub release immutability freezes assets the moment a release is
> published, so the installer can never be attached to that tag. Do not
> attempt to re-tag or re-run the build for `v0.2.0` — CI now refuses to
> build over an already-published tag. `v0.2.1` is the auto-update floor.

### Fixed

- **Google Calendar was missing from the build.** CI installed only the
  `[rag]` extra, so PyInstaller froze a backend with no `google_auth_oauthlib`
  and users got a 501 telling them to run `pip install` — into an app with no
  pip. The `gcal` extra is now installed at build time, both connector clients
  are bundled explicitly, and their packaging metadata is *required* rather
  than silently skipped, so a build missing them fails instead of shipping.
- **Todoist was declared nowhere.** `todoist-api-python` was imported
  unconditionally but absent from every dependency list; it only worked on a
  machine where it had been installed by hand. It is now a declared
  dependency.
- **Connecting Todoist took down the app.** Neither the missing-library
  `ImportError` nor any API error was caught by any caller, so a stored token
  turned `/api/agenda`, `/api/tasks`, the study signals and the planner into
  500s. Google Calendar had the identical exposure, plus two more call sites.
  Both connectors now degrade instead of failing, and Integrations reports a
  real `failing` state instead of inferring "wired" from a keyring entry.
  Connecting no longer stores a token it could not verify.
- **The vault was never indexed.** `reindex_all` and `watch_vault` had two
  call sites, both in the CLI — no scheduler job, no trigger from the desktop
  shell, and the command palette's "reindex" was a fake toast. The collection
  stayed empty, and an empty collection short-circuits silently, which is why
  chat could never retrieve from Obsidian. Adds `POST /api/index/reindex` and
  `GET /api/index/status`, a boot-time index and live file watcher, and a
  nightly self-heal job.
- **RAG failures were indistinguishable from an empty vault.** Broad
  `except Exception` at every boundary turned "extras not installed" and
  "never indexed" into "no results". Failures are now logged and surfaced, and
  `argus doctor` reports real chunk counts instead of checking that a
  directory exists.
- **Retrieval quality**: chunking collapsed every newline, destroying bullets,
  tables and code blocks; chunks dropped their own heading text; inline
  Obsidian `#tags` were ignored; wikilinks were resolved with a full-vault
  scan per link and could not follow aliases; and the BM25 corpus was rebuilt
  from the entire index on every query.
- **Study uploads appeared to do nothing.** Files were sent to the course
  root while materials are only counted in `<course>/materials/`, so the row
  kept reading "0 materials" with GUIDE and EXAM disabled. Uploaded PDFs were
  also invisible in the sources rail, and its dropzone had no handler at all.
- **Deleted study data came back.** The course `×` was in-memory state that a
  reload undid; there were no delete routes for exams or decks, so orphaned
  rows kept listing; and every new vault was seeded with a CS000 sample
  course. Deletion is now real, confirmed, and cascades.
- **Research deletion did nothing.** The page was entirely client-side mock
  state — `×` filtered a local array, and no paper was ever really created.
  Papers and highlights are now ordinary vault notes.
- **The AGENT.USAGE panel header overflowed.** It carried a 32px button and a
  26px switcher in a non-wrapping row, needing 336px of a 300px rail (377 of
  337 at large UI scale).
- **A missing `git` binary surfaced as a 500** from every write route rather
  than a message naming the prerequisite.
- **`.eml` files saved but never indexed** — the ingest route accepted them
  while no extractor was registered.
- The MCP snippet handed to coding agents omitted `ARGUS_ENV_FILE`, so a
  spawned server could not find the vault and exited.
- The Google OAuth client file was resolved relative to the working
  directory, which in the packaged app is inside `Program Files`.

### Added

- **Configurable vault taxonomy.** Argus hardcoded nine PARA folder names in
  54 places, so pointing it at an existing Obsidian vault meant quick capture,
  briefings, course discovery and the private-zone guard all targeted folders
  the user had never created. Folder names now come from `VAULT_*_DIR` keys in
  the config file. Every default is the previous name, so existing installs
  are unaffected.
- `--selftest-imports` on the packaged backend, gating releases on every
  lazily-imported optional dependency actually being present.
- A CI workflow running the Python and frontend test suites. Neither had ever
  run in CI, which is why v0.2.0's regressions were invisible.
- A version-drift check: `pyproject.toml`, `web/package.json` and
  `desktop/package.json` all claimed 0.1.0 while v0.2.0 was the shipped tag.
- A staleness guard for the staged dashboard, matching the existing one for
  the backend — v0.2.0 shipped a UI predating its own last commit.

## [Unreleased]

### Added
- **Pluggable model backends** — chat, the day planner and study generation now run on whichever model you register: a local [Ollama](https://ollama.com) model, any hosted OpenAI-compatible provider (Groq, Together, Fireworks, OpenRouter, DeepInfra), the Anthropic API on a key, or Claude Code. **Argus no longer requires Claude Code.** One `ToolSpec`/`AgentAdapter` abstraction (`backend/agent/adapters.py`) backs all four; the same tool handlers run whichever provider dispatches them.
- **Guided model configuration** — **System → MODELS → + ADD MODEL** walks through provider, credentials and model choice, and will not save a configuration that hasn't passed a live **Test connection**. The test lists the models an endpoint actually serves, so the model is a dropdown rather than something you have to know. Keys are stored in the OS keyring, referenced by name only in `.argus/models.json`.
- **Hardware-aware local model picker** — **System → LOCAL.MODELS** detects RAM and NVIDIA VRAM, labels each curated model **FITS** / **SLOW** / **TOO BIG** with a plain-language reason, marks a best fit, and installs it through Ollama with live progress. Every catalogued model supports tool calling.
- **`argus mcp-server`** — exposes the vault's read-only tools (`search_vault`, `read_note`, `list_tasks`) over MCP so Claude Code, Codex CLI or Gemini CLI can use your notes from any project directory. The planner's `propose_*` tools are deliberately not exposed.
- **Settable default model** — `POST /api/models/default`, persisted in `.argus/model-prefs.json`. The default was previously pinned to a Claude model with no way to change it.
- **LOCAL / HOSTED badges** on every model row and in the model picker, so whether your notes leave the machine is visible at a glance.
- Model selection now reaches `/plan` and study guide/exam generation, not just chat. The Study tab gains the model selector chat already had.
- `argus doctor` reports Ollama reachability (`WARN`, never `FAIL` — it's one option, not a requirement).
- **Quick Links** — a configurable panel on the Dashboard tab to save and open frequently-used sites (icon glyph + label + https URL), persisted in the backend SQLite store. URLs are sanitized to https-only.

### Changed
- The desktop installer no longer blocks on Claude Code. Only git is required; Claude Code and Ollama are reported as optional, and a new onboarding step explains the three ways to run the AI.
- `list_tasks` returns real vault tasks. It had been a stub replying "the task engine is not built yet (Phase P2)" long after `backend/tasks/` shipped — and it is one of the tools the MCP bridge exposes.
- Token-usage cost estimates price unknown models at **zero** and name them in `unpriced_models`, instead of falling back to Opus rates. Billing a free local model at $15/M would have been actively misleading. `argus`'s Claude Code CLI usage report is unaffected — every model there genuinely is an Anthropic one.
- `httpx` and `mcp` are now explicit dependencies rather than transitives of `claude-agent-sdk`.

### Fixed
- Dev-mode CSP now includes `'unsafe-eval'` **in development only**, so `next dev`'s React Refresh runtime can hydrate client components (previously all dev-mode client hydration silently failed). Production and packaged builds keep the strict, eval-free policy. This also unblocks the Playwright e2e suite.
