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
- **D-005 — Python env.** `uv` not installed; standard `python -m venv .venv` + pip.
  Heavy RAG deps (chromadb, sentence-transformers/torch) are an optional extra
  (`[rag]`) installed at P1 so P0 stays fast and open-source setup stays minimal.
