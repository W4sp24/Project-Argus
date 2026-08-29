# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Argus is currently pre-1.0 (0.x releases).

## [0.3.1]

Sixty-one commits since v0.3.0. Argus gains a corpus you can see and steer:
`/sources` lists every real file in the vault, ingest is one path with a
destination you choose, and long work runs on a durable job model that survives
a restart. Generated study material renders mathematics. The last stretch of
this release acted on a full UX audit of the ingest surface — every finding
closed, with each regression test verified to fail against the code it fixes.

### Added

#### Sources and ingest

- **`/sources` — see the corpus and steer what enters it.** A folder rail, a
  searchable and sortable file list, and a view that lives in the URL. Files
  Argus wrote say so on the wire rather than by filename guess, "not indexed" is
  something you can act on, and a running job survives navigating away.
- **Sources can be deleted**, one at a time or in a batch, and a deletion removes
  the file from the vault *and* its chunks from the index in the same operation.
  A deleted note stops being retrieved and cited.
- **One ingest path with a destination you choose.** The batch job saves, indexes
  and summarises a set of files, records what it did to every one of them, and
  reports where a file stopped when it stopped early. The dialog's destination is
  the destination it writes to, and the limits it shows are the limits the server
  enforces.
- **A file tagged `#no-ai` is never sent to a model to be summarised.**
- **Four note shapes**, built on how people actually learn, chosen before ingest
  and recorded on the job; a course note lands in `notes/`.

#### Jobs

- **One durable job model for every piece of long work.** `ingest`, `reindex`,
  `guide` and `exam` share a job store with a `kind` and its parameters. Work is
  visible while it runs, survives navigation, and contends by group: ingest and
  reindex share one slot because both load the embedding model and write the same
  collection. `POST /api/index/reindex` takes optional paths for a scoped rebuild.

#### Study

- **Generate from the sources you picked.** The Course Hub's checkboxes decide
  what gets read, retrieval can be pinned to a hand-picked set of files, and every
  artifact Argus writes is reachable the moment it is written.
- Shift-click ticks a run of sources; the hub shows one pane at a time on a narrow
  screen.

#### Mathematics

- **Mathematics renders**, behind one markdown boundary and one prose stylesheet.
  Exam and flashcard surfaces render notation accessibly, and the exam writes
  notation where notation is safe and nowhere else.
- One output-format contract wherever a model writes prose.

### Changed

- `list_files` lists every real file, not just markdown; course sources are one
  zone-stamped filter rather than a second walker.
- A batch of writes takes one undo point instead of one per file, and
  `delete_note` can take its snapshot once for a batch — concurrent git snapshots
  race on `.git/index.lock` and the loser fails silently.
- The embedding model is loaded once, not once per request, and chunk counts are
  read without fetching the chunks' text.
- `/dashboard` is back inside the first-load budget.

### Fixed

- **A second guide the same day no longer destroys the first**, and a guide
  containing a code fence is no longer replaced by it.
- **The Course Hub opens with its sources selected**, and ALL means what you can
  see. A late-arriving locked target is applied rather than silently ingesting
  outside the course.
- The job panel stops claiming it is still ingesting; the folder rail stops
  deleting its own siblings; the ingest dialog stops promising things it does not
  do; STUDIO stops hiding its own output and Argus stops re-reading itself.
- LaTeX in a generated exam survives JSON parsing, a display equation is one
  chunk, a `#` inside a fence is not a heading, grading reads notation as
  notation, and a half-streamed equation no longer flashes red.
- Stripping a citation marker no longer flattens the answer, and the briefing
  prompt stays out of notation it cannot render.
- The faintest tier of the palette clears AA against `panel` — not only against
  the page background — and a row is a real touch target.

## [0.3.0]

A feature release: 170 commits since v0.2.1. Argus stops being a Claude
Code-only lookup box. It gains a real agentic chat with threads and a visible
tool trace, model backends you choose per provider, and n8n automations as a
first-class mode. `v0.2.1` remains the auto-update floor.

### Added

#### Chat and the agent

- **Conversations you can come back to.** Threads and messages get tables of
  their own, are readable over REST, and one socket now carries a thread, its
  history and its tool trace instead of a bare prompt-response pair. The Study
  tab's Course Hub stops re-implementing chat and uses the same surface.
- **The agent narrates what it is doing.** Tool events bracket each call, the
  vault tools describe what they did, and citations come from the trace rather
  than from a regex over the answer. Answers render as markdown instead of
  literal asterisks.
- **The agent can browse the vault, not only search it.** `list_notes` joins
  `search_vault` and `read_note`, and a guarded public `edit_note` takes a
  snapshot before it writes.
- **Multi-provider models.** Gemini speaks its own API, Gemini and DeepSeek are
  one click away in the model picker, and the usage dashboard prices the models
  people are actually buying.
- The chat prompt grounds an agent rather than a lookup box, and tells the model
  how to look, not just that it should.

#### Automations

- **Automations is a sixth mode.** A rebuilt `/automations` around registered
  n8n instances, one list and real activity; a connect dialog covering instance,
  test, inbound and discover; and the command palette can run automations rather
  than only fire them.
- **More than one n8n instance.** A list-shaped instance registry, per-instance
  authentication of the inbound surface, and every record attributed to the
  instance it came from.
- **Ambient surfaces on the dashboard.** An auto-placed widget grid you can take
  control of, per-widget layout, widget chrome with origin, honest `STALE` and
  reassuring `EMPTY` states, and an automations readout in the status line.
- **Shipped workflow templates and an install flow**, plus activity events, run
  cancellation, and a form-trigger schema with widget validation behind one
  sanitiser.
- Registered automations are callable from chat.
- The inbound surface runs on its own loopback port and is authenticated.

#### Vault, study and research

- **Configurable vault taxonomy** threaded through every call site.
- **The vault is actually indexed** — `POST /api/index/reindex`, boot-time
  indexing, a live file watcher and a nightly self-heal job. The command
  palette's reindex action reaches the real API.
- The Course Hub is wired to real chat, generation and course-scoped retrieval;
  a course-sources endpoint, a real dropzone and a due-cards summary; and real,
  persistent deletion for courses, exams and decks.
- The reading queue and highlights persist to the vault.
- `GET /api/notes` gains a folder filter and a frontmatter whitelist.
- Todoist tasks can be added and completed from `TASKS.DUE`; `PLANNER.TIMELINE`
  gains day navigation, add-event and real states.

#### Models, MCP and the desktop shell

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
- `mcp` is capped to `<2.0` so a major release cannot break the bridge.
- Frozen desktop builds are gated on connector imports actually working.

### Fixed

- **Retrieval tells the truth.** RAG no longer swallows real search, ingest and
  warm-up failures; the rerank setting actually reranks; chunking preserves
  structure, captures inline tags and resolves links properly.
- **The model that runs is the model we label.** Turns abandoned mid-stream still
  report the tokens they spent, a turn that runs out of steps says so and spends
  its last one answering, and hosted providers are asked for the token counts
  they have.
- **A tool call is a tool call.** One the model prints as text is no longer
  rendered as the answer, and a call that went wrong comes back as an
  explanation instead of a dead end. `read_note` obeys the whole of I3 and stays
  inside the vault; deep links stop guessing the vault's registered name.
- **Automations that were shipped unrunnable.** Every bundled template failed;
  templates now push the fields the cards need, widget validators no longer drop
  every field the cards read, the Todoist node stops calling an API that 410s,
  installing a template that needs a credential no longer 502s, and tags are
  applied after create because n8n refuses them on it.
- **Connection diagnostics that blamed the wrong thing.** A wrong key is told
  apart from a wrong URL, a failed connection test says so, `TEST CONNECTION` no
  longer sits disabled with nothing explaining why, and no inbound URL is handed
  out without a host.
- Undated external tasks never reached the agenda. An unreadable keyring took
  down the dashboard. The dashboard failed hydration on every load, and the saved
  interface size was lost whenever React rebuilt the root. The resize control was
  unclickable on narrow widgets, `/automations` was unreachable without knowing
  it existed, and `AGENT.USAGE`'s header overflowed the rail panel.
- Writes wait for the write lock instead of 500ing.
- Dev-mode CSP now includes `'unsafe-eval'` **in development only**, so `next
  dev`'s React Refresh runtime can hydrate client components (previously all
  dev-mode client hydration silently failed). Production and packaged builds keep
  the strict, eval-free policy. This also unblocks the Playwright e2e suite.
- Study uploads target each course's real `materials/` path, the course × button
  actually deletes, and the CLI stops seeding a permanent `CS000` sample course.
- `argus doctor` reports Chroma health from the actual index rather than the
  directory; ingest surfaces a missing git binary and actually indexes `.eml`;
  the MCP snippet carries the resolved env file.
- The test suite means the same thing on Linux as on Windows, and no longer
  depends on the machine having a git identity.

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

- **The packaged app could never index or search — the embedding model was
  not importable.** The PyInstaller spec excluded `torch.distributed`,
  `torch.testing` and `torchgen` to save disk, but sentence-transformers
  imports all three at module scope, so `import sentence_transformers` raised
  `ModuleNotFoundError` inside *every packaged build ever produced*. No
  embedding model meant no indexing and no retrieval, independent of the
  reindex wiring below. It survived undetected because the release gate only
  asserted that `GET /api/search` returned HTTP 200, and a swallowed exception
  returns 200 with an empty list — and because it cannot reproduce in a dev
  checkout, which never goes through PyInstaller. The smoke test now
  round-trips a real reindex and requires an actual search hit.
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

