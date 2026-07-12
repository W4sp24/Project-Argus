# FRIDAY — Build Log

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
