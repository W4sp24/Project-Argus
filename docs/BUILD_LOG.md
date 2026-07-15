# Argus — Build Log

Decision log. Per the Master Prompt: questions a spec reader could answer are decided
here, not asked.

## 2026-07-12

- **D-001 — Repo location & init.** Playbook §1 expects a pre-created GitHub repo with
  the PDFs already in `docs/`. Reality: `Project-Argus/` contained only the P0.5
  addendum PDF and no git repo. Decision: `git init -b main` in `Project-Argus`,
  copy all three PDFs into `docs/`. Pushing is blocked until a remote exists
  (`gh` CLI not installed) — recorded in BUILD_STATE blockers. Conventional commits,
  feature branches per phase, merged to `main` at each phase gate.
- **D-002 — Three input documents, precedence.** Spec v0.2 defines WHAT, Playbook v1.0
  defines HOW, Master Prompt invariants I1–I6 + dev-loop invariants D1–D4 win over
  both. The P0.5 addendum inserts a phase between P0 and P1.
- **D-003 — Visual direction (user requirement, supersedes default Tailwind look).**
  Purple-base palette, gradients, moderate futurism, glassmorphism. Applied as a
  design-token layer in `web/` so every page shares one visual language.
- **D-004 — Linting/formatting.** Spec mandates PEP 8 / conventions but pins no tool.
  Decision: `ruff` (lint + format, line length 100) for Python; ESLint (next/core-web-vitals)
  + TypeScript strict for web. Enforced before each commit.
- **D-006 — Plan execution mode.** Playbook prescribes subagent-driven development;
  executed P0 inline instead (full context already in-session; subagent dispatch adds
  cost without review benefit at this scale). TDD red→green kept per task.
- **D-007 — Phase-gate review tooling.** `/code-review` resolves to the CodeRabbit CLI,
  which is not installed and requires interactive auth. Substituted a manual diff
  review for P0 (no confirmed defects; noted: `_write_env` drops comments from `.env`,
  Today greeting hardcodes the owner name pending a config surface). Install
  `coderabbit` CLI to restore automated gates.
- **D-008 — `.env` points at the real vault.** VAULT_PATH set to
  `C:\Users\ethan\Documents\Scientia` per the addendum ("Scientia is FRIDAY's
  VAULT_PATH"). `demo-vault/` is disposable test output, gitignored.
- **D-009 — Scientia scaffold.** Scientia was a flat 2-file vault; added the PARA
  zones non-destructively (existing files untouched, root daily note left in place
  for Ethan to move) plus `90-Meta/`, and git-initialized the vault (I2 groundwork).
- **D-010 — Hook wiring is user-run.** The permission classifier blocks Claude from
  editing `~/.claude/settings.json` to install auto-run hooks (self-modification
  guardrail). Shipped `claude-integration/setup.ps1` instead: installs hooks +
  /log-session and merges the settings snippet non-destructively. Hook scripts and
  the command ARE installed to `~/.claude/{hooks,commands}` already; only the
  settings.json wiring needs Ethan.
- **D-011 — no-ai matching is substring-conservative.** A note whose *body text*
  mentions the literal `#no-ai` tag is treated as private (false-positive-safe,
  false-negative-unsafe). Docs in the vault reference the tag as "no-ai" without
  the hash to stay visible.
- **D-012 — /log-session works MCP-or-direct.** Addendum D3 prescribes the Obsidian
  MCP for narratives; the command allows direct file writes as fallback so the loop
  works before the REST plugin exists. The deterministic stub is hook-written either
  way.
- **D-013 — 90-Meta excluded from RAG.** The journal zone is served by the journal
  API (D2); keeping it out of the RAG index prevents dev-session noise from
  polluting daily-life retrieval. Revisit if journal-aware chat is wanted later.
- **D-014 — Chat runner is dependency-injected.** `/ws/chat` takes an async-iterator
  runner; tests stream canned deltas without touching the agent SDK. The real
  runner background-warms the embedding index at first connection (cold model load
  inside a tool call blocked the agent's event loop for ~20s).
- **D-015 — Token-count proxy in chunker.** Playbook targets ~350 tokens/chunk;
  implemented as 260 words (≈350 tokens) with 40-word overlap — avoids a tokenizer
  dependency for marginal precision.
- **D-016 — Citation enforcement is quote-based.** Exam questions must carry a
  verbatim quote; validation normalizes and substring-matches against the cited
  chunk. Stricter than path-only checking — catches right-file/wrong-fact
  hallucinations.
- **D-017 — Syllabus parsing is deterministic (regex), not agent-based.** Dated
  deadline lines are extractable without a model; keeps the import instant, free,
  and unit-testable. An agent pass can be layered on later if recall disappoints.
- **D-018 — Study generator injected app-wide.** Same DI pattern as the chat
  runner: `create_app(generator=, index_factory=)` so /api/study tests run on
  fakes; the real generator is a one-shot tool-less agent query.
- **D-019 — Vault git via subprocess, not GitPython.** Playbook names GitPython;
  `writer.py` and `cli.py` already shell out to git consistently, one less dep.
- **D-020 — Connectors degrade gracefully.** gcal/todoist return `[]` +
  `configured: false` until credentials exist, so the app is fully usable
  offline and the human OAuth stop doesn't block the phase.
- **D-021 — Single-writer proof is a test.** `test_single_writer_source_proof`
  scans backend sources: only writer.py (plus the cli installer and the study
  I1-exemption) may combine inbox references with write calls.
- **D-005 — Python env.** `uv` not installed; standard `python -m venv .venv` + pip.
  Heavy RAG deps (chromadb, sentence-transformers/torch) are an optional extra
  (`[rag]`) installed at P1 so P0 stays fast and open-source setup stays minimal.
- **D-022 — Suggestion queue = existing table + one migration.** P1.5's syllabus
  import already used `suggestions`; P3 layers a typed model (`backend/suggestions.py`)
  over it and adds `dismiss_reason` via an idempotent `ALTER` in `init_schema`, so
  pre-P3 databases migrate on first touch. Dismissal reasons are fed back into the
  planner context so it stops re-proposing rejected ideas.
- **D-023 — Drift-safe applies.** Task edits verify `old_line` (strip-compared) at
  the exact line; note edits are unified diffs applied with full context/`-` line
  verification. Any mismatch raises `WriterError` BEFORE the file is written — the
  file stays untouched and the row stays pending, surfaced to the UI as a 409.
- **D-024 — Planner is propose-only by construction (I1).** The planner agent gets
  ONLY the three `mcp__planner__propose_*` tools (Bash/Write/Edit/Read/Glob/Grep
  disallowed); its context (agenda, task buckets, review queues, preferences note,
  dismissal feedback) is assembled server-side rather than giving the agent read
  access. Its only side effect is rows in the suggestions table.
- **D-025 — gcal scope readonly → events.** `apply_suggestion(schedule)` needs
  insert; FRIDAY-created blocks carry `colorId 3` (grape) + a description marker so
  they're visually distinct and machine-identifiable. The stored OAuth token (when
  credentials arrive) must be granted the wider scope at connect time.
- **D-026 — Preferences note is vault-owned config.** `30-Areas/assistant-preferences.md`
  (seeded in vault-template and Scientia) is read by the planner every run — editing
  a note in Obsidian IS configuring the assistant; no settings UI needed.
- **D-027 — Audit stores path lists ONLY (I3).** Every agent entry point (chat
  search/read, planner context, briefing composer, exam generation) logs entry point +
  model + sorted path set to the `audit` table; prompt text is never persisted.
  Logging is best-effort by construction — a broken audit can never break chat.
- **D-028 — Briefing composition is one opus pass with a deterministic net.**
  Playbook names haiku for summarization steps; skipped — the inputs are small
  structured facts, not documents. Any composer failure (or empty output) falls back
  to `render_briefing`, so the 07:00 job can never produce nothing.
- **D-029 — Scheduler attaches only to the module-level app.** `create_app` takes a
  `scheduler_factory`; only `backend.main`'s production instance passes one, so no
  test ever spawns background threads. Phase review caught that the production
  factory forgot the agent composer (cron briefings would have used the fallback) —
  fixed with a regression test.
- **D-030 — E2E provisions its own vault inside the webServer command.** The
  Playwright backend command (`e2e/start-backend.mjs`) creates a throwaway vault via
  `friday init` + seeds a suggestion, then runs uvicorn from a workdir whose `.env`
  points at it; `reuseExistingServer: false` guarantees a dev backend aimed at a REAL
  vault can never be picked up by tests.
- **D-031 — P4 review gate.** CodeRabbit CLI still not installed; performed a manual
  diff review of the phase (1 CONFIRMED finding, fixed — see D-029). `/code-review
  ultra` on the full repo is user-triggered/billed and left to Ethan as an optional
  follow-up.
- **D-032 — Rebrand FRIDAY → Argus.** QA/UX/performance pass requested by Ethan
  (repo was already named Project-Argus; product surface still said FRIDAY
  everywhere). Renamed: CLI (`friday` → `argus` console script, package name in
  `pyproject.toml`), git commit prefix (`friday:` → `argus:`), daily-note audit
  heading (`## FRIDAY log` → `## Argus log`), the vault's hidden storage folder
  (`.friday/friday.db` → `.argus/argus.db`, `.friday/chroma` → `.argus/chroma`),
  keyring service names (`friday-*` → `argus-*` — safe, no connector was connected
  yet so nothing persisted under the old names), the `FRIDAY_VAULT` env var
  (→ `ARGUS_VAULT`, also unset so no migration needed), agent persona strings in
  `backend/agent/prompts/*.md` and `backend/briefing.py`, and all UI copy/titles.
  The real Scientia vault's `.friday/` folder was copied (not deleted) to `.argus/`
  so the existing suggestion history and RAG index carried over — `argus doctor`
  and a live `/review` check both confirmed the migrated data. Left untouched:
  the three PDF spec files (binary, filenames cross-referenced elsewhere) and the
  quoted historical evidence blocks in this log / BUILD_STATE.md / docs/plans/ —
  those are a record of what the tests actually printed at the time and would be
  inaccurate if rewritten; only their headers were updated.
- **D-033 — Study-page drag-and-drop was missing; mobile nav overflowed.** Same QA
  pass. The Study page copy promised "Drop slides and syllabi into a course" but
  had no `onDrop` handler (click-to-browse only) — likely the source of "can't add
  files" — fixed with real drag-and-drop + a file-type check. Separately, the
  7-item bottom nav overflowed at common phone widths (measured: "Insights" fully
  off-screen at 390px), fixed by switching to icon-only nav below `md`. Also
  code-split `/insights` (recharts) and `/journal` (react-markdown) via
  `next/dynamic`, cutting first-load JS from 205 kB/129 kB to ~96 kB each.
- **D-034 — Direct user CRUD via the writer with CAS drift checks (vs a review
  queue for humans).** P4's suggest-then-approve queue exists because an *agent*
  can't be trusted to write unsupervised; a human clicking edit/delete on their
  own note doesn't need a second human to approve it. `update_note`/`delete_note`
  and `update_task_line`/`delete_task_line`/`toggle_task_line` go straight
  through `backend/writer.py` (I1) with the same git pre-apply snapshot (I2) as
  every other write, but compare-and-swap instead of a queue: every mutation
  carries the content (or exact line) the client last read, and a
  `WriterConflict`/409 is raised — file untouched — if it drifted underneath
  them. AI-initiated changes keep the Review queue; this is human-initiated only.
  `guard_user_path` refuses `99-Private/`, `90-Meta/` (D1), traversal, and
  absolute paths server-side regardless of what the UI sends.
- **D-035 — Heatmap counts tasks + notes + study + captures, with note events
  sourced from vault git history.** Task completions and study attempts already
  live in structures that carry a date (`✅` stamps, `attempts.created_at`); note
  creates/edits don't, so `_note_touches_by_day` walks `git log --since --name-only`
  on the vault itself rather than adding note-level timestamps to track. This
  reuses I2's snapshot trail as the activity signal instead of a new column, and
  `EXCLUDED_TOP_DIRS` filtering on the git log output keeps `99-Private/` out of
  the grid (I3) the same way the RAG indexer excludes it.
- **D-036 — `argus web` is the production launcher and now the daily entry
  point.** `npm run dev` compiles on every route hit — Ethan's "laggy" report
  traced to the dev compiler, not the app. `argus web` builds once (or on
  `--build`/missing `BUILD_ID`) and runs `uvicorn` + `next start` side by side,
  Ctrl-C tearing down both; the README's Quickstart now leads with it and the
  old two-terminal `uvicorn` + `npm run dev` flow moved under Development.
- **D-037 — One `ChatProvider`, two surfaces (dock + tab).** The `/ws/chat`
  session and message history were page-local state on `/chat`; lifting them
  into a React context mounted at the dashboard layout means the always-visible
  dock in the right rail and the full `/chat` page read the same conversation —
  switching surfaces mid-answer doesn't drop or duplicate anything. Kept to one
  socket per session rather than one per surface.

## 2026-07-15 — Terminal-HUD redesign (Phases A–H)

- **D-038 — Mode system: CSS custom properties, not per-page themes.** Four modes
  (general/study/research/code, plus system) replace per-page theming. A single
  `ModeProvider` sets `--ac`/`--ac-bg` on one wrapper div; every accent-colored
  element uses `text-[var(--ac)]`-style arbitrary values. One style recalc per
  mode switch, zero re-renders of unaffected components, and route → mode is
  derived from the pathname so deep links and back/forward always resolve the
  right accent. The Sidebar is replaced by a sticky TopBar tablist (now with
  APG roving tabindex, Phase H); old per-page nav lives on as General-mode
  panels + palette actions, and the old routes stay alive behind stat-tile
  links.
- **D-039 — Legacy-alias migration ran two-phase by design.** Phase A kept
  `primary/accent/signal/nebula`, `rounded-glass`, and `font-display` as
  LEGACY aliases in tailwind.config.ts because Tailwind silently drops
  unknown classes — deleting the names before the re-skin would have broken
  every page with zero build errors. Phase H did the delete: first converted
  every remaining usage on /tasks, /journal, /review, /insights and the error
  boundary to the new tokens (grep-verified zero leftovers), re-pointed the
  /insights recharts palette (chartTheme.ts) at the new hex values, then
  removed the aliases. `borderRadius.full` survives for the circle motif
  (logo dot, round checkboxes, avatar orb).
- **D-040 — Preview-flag discipline (§8) held, and Phase H is the payoff.**
  Every not-yet-wired panel shipped UI-only behind `flags.* = "preview"` with
  a PREVIEW badge and a grep guard (no `fetch(` inside components/preview/).
  Phase H flipped tokenUsage and emailCapture to "enabled" by replacing mocks
  with the real endpoints, and *moved* DoctorPanel/ModelsPanel out of
  preview/ into system/ the moment they gained fetches, keeping the guard
  meaningful. Still preview: flashcards, library, focusTimer, activeWork,
  courseCreate, courseHub, localModels (registration is real; server-side
  routing to local endpoints is not), and the palette's reindex row (no HTTP
  endpoint exists).
- **D-041 — Note creation is a writer-gated create-ONLY path.** The quick-note
  modal needed title-derived files (`00-Inbox/YYYY-MM-DD-<slug>.md`), which
  `PUT /api/note` (CAS update of an existing file) can't do. New
  `writer.create_note` + `POST /api/note/create` (201): snapshot-first (I2),
  `guard_user_path` (I3), and it refuses existing files (`WriterExists` →
  409) instead of dedupe-renaming like ingest does — the user picked that
  exact filename, so the client resolves collisions explicitly (numbered-slug
  retry) rather than the server silently renaming.
- **D-042 — gcal import guard + [gcal] extra (fresh-venv 500s).**
  `gcal._service()` imported google-api-python-client/google-auth before
  checking whether a token was even stored, so /api/agenda and /api/insights
  raised ImportError in every fresh venv — deps that were declared nowhere.
  Chose the guard over making them base deps: the base app must work fully
  offline/unconfigured (D-020), and the google stack is heavy. `_service()`
  now checks the keyring token first (unconfigured → `[]`, libs untouched);
  the libs are declared in a new `[gcal]` extra needed only to actually
  connect. Regression tests block google imports to simulate the fresh venv.
- **D-043 — E2E roundtrip flake root cause: live-agent warm-up stall.** The
  capture→approve→vault roundtrip spec intermittently timed out because the
  dashboard chat spec runs first and its ws message starts `ChatAgent.warm()`
  (`_default_chat_runner`, backend/main.py) — the embedding-model cold load
  stalls the backend event loop and slows frontend hydration well past the
  default 5s polls. Fixed in Phase F (1d3a484) two ways: the capture step
  re-fills until Save actually enables (a fill landing before hydration
  updates the DOM but not React state, leaving Save disabled forever), and
  the vault-write polls get explicit 30s timeouts (the writer request queues
  behind the busy loop; the write lands, just late). The production behavior
  was already correct (warm-up runs on a daemon thread, D-014) — the test
  just assumed a cold backend responds instantly.
