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
- **D-005 — Python env.** `uv` not installed; standard `python -m venv .venv` + pip.
  Heavy RAG deps (chromadb, sentence-transformers/torch) are an optional extra
  (`[rag]`) installed at P1 so P0 stays fast and open-source setup stays minimal.
