# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Argus is a local second-brain over an Obsidian vault: FastAPI backend
(`backend/`), Next.js 14 dashboard (`web/`), Electron desktop shell
(`desktop/`). `README.md` has the layout and quickstart; **`CONTRIBUTING.md`
has the branching model and the whole CI/CD story in five scenarios** — read it
before touching a workflow or cutting a release. This file is what neither of
them records.

## Commands

**Use the venv's Python. Bare `pytest` fails collection** with
`ModuleNotFoundError: No module named 'backend'` — the global interpreter has
no editable install.

```bash
.venv/Scripts/python -m pytest                     # backend suite (~7 min)
.venv/Scripts/python -m pytest tests/features/flashcards/test_cards.py::test_starring_persists
.venv/Scripts/python -m ruff check .               # repo-wide, no allow-list
```

`ruff format` is **not** gated by CI. Do not reformat files your change does
not otherwise touch.

Four test runners, each with a different job:

```bash
.venv/Scripts/python -m pytest                     # backend
cd web && npm run test:unit                        # Vitest — PURE logic in lib/ only, node env, no DOM
cd desktop && node --test                          # desktop shell helpers, zero-dep node:test
cd web && npm run e2e                              # Playwright (needs ports 8000 and 3100 free)
cd web && npx playwright test notebook.spec.ts -g "one test name"
```

Components are **not** unit-tested — they are covered by Playwright against a
real backend, which is the only place their SWR and WebSocket behaviour is
real. Keep providers thin enough that this stays honest.

Development runs the two servers separately for hot reload; `argus web` is the
production launcher:

```bash
uvicorn backend.main:app --port 8000
cd web && npm run dev
```

The full local gate, identical to what CI runs (see `CONTRIBUTING.md`
§Scenario 2):

```bash
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m pytest
.venv/Scripts/python desktop/tests/smoke_backend.py --target desktop/backend/argus_server.py
cd desktop && node --test
cd web && npx tsc --noEmit && npm run lint && npm run test:unit && npm run build
node desktop/scripts/check-versions.mjs
cd web && npm run e2e
```

## Invariants

The source references these **by number** across ~20 modules with no central
definition. This is the decoder ring.

| | |
|---|---|
| **I1** | Every vault write goes through `backend/vault/writer.py`. The planner never writes — it queues suggestion rows a user approves. *One sanctioned exception:* new files under a course's `study/` folder. |
| **I2** | The writer git-commits the vault before each apply, so any write is undoable. The vault must be a git repo; the writer raises if it is not. |
| **I3** | `99-Private/` and any `#no-ai` note are never indexed and never sent to a model (`backend/vault/privacy.py`). |
| **I4** | Secrets live in the OS keyring only — never the repo, never the vault. |
| **I5** | Omitting `model` keeps the Claude Code subscription path (no API key); naming a registry model routes through `backend/agent/adapters.py`. |
| **I6** | Agent tools are read-only and every result carries citation metadata. Output whose citation cannot be verified is dropped, not shipped. |

## Architecture

**The dependency rule is one-way.** `core/` `vault/` `telemetry/` (platform) →
`agent/` `rag/` `connectors/` (capabilities) → `features/` (one package per
feature, each a `router.py` plus its own modules). A feature may import
anything above it; **nothing above may import a feature, and no feature imports
another.** Only `main.py`, `cli.py` and `scheduler.py` know features exist.

**Never hardcode a vault folder name.** Go through `core/taxonomy.py`'s derived
properties (`tax.courses`, `tax.course_study(code)`, …). The nine zones are
configurable, and `Taxonomy` rejects duplicate names because aliasing anything
onto `private` would breach I3. A hardcoded `15-Courses` is a bug that has
shipped here before.

**Long-running work goes through the shared job store.** `ingest_jobs` carries
`kind` + `params` and serves ingest, reindex, relink, guide, exam and deck.
Such an endpoint answers `202 {"job_id": …}` and is polled at
`GET /api/ingest/jobs/{id}`; contention is by slot group
(`features/ingest/store.py::SLOT_GROUPS` — ingest/reindex/relink share the
`index` slot because they all load the embedding model; generation takes none).
On the frontend these are owned by `JobsProvider` (`web/lib/jobs.tsx`), mounted
**above the router** in `(dashboard)/layout.tsx`, and recovered from the server
rather than from `localStorage`. Holding a long request in a component local is
the bug that machinery exists to prevent.

**One SQLite database**, at `<vault>/.argus/argus.db`. Migrations are additive
`ALTER`s inside `core/db.py::init_schema`, which runs on **every** connection —
so they must be idempotent, and any backfill belongs *outside* the
"column is missing" branch, or it is correct exactly once.

**The desktop shell supervises three processes**, and the dashboard is
**cross-origin when packaged**: the backend port is chosen at launch, and Next
bakes `rewrites()` at build time, so they cannot carry it. The shell injects
the origin through `preload.js`.

> **Always use `apiFetch` / `mutateJSON` / `fetcher` from `web/lib/api.ts`.** A
> bare `fetch("/api/…")` works in dev and 404s **only when packaged** — the
> worst possible shape for a defect.

Same class of trap in `desktop/main.js`: a second window must re-declare
`preload` and `additionalArguments`, because dev is same-origin through the
Next rewrite and would work without them.

## Frontend conventions

Enforced by review, invisible to tooling.

- **Named type scale only** — `text-micro`/`meta`/`label`/`body`/`lead`/
  `title`/`display`. Never `text-[13px]`; ~320 arbitrary sizes were codemodded
  away.
- **Never `focus:outline-none`.** Tailwind emits it at specificity (0,2,0),
  which beats the bare `:focus-visible` rule in `globals.css`.
- `components/ui/Dialog.tsx` is the **only** overlay implementation (focus
  trap, Escape stack, refcounted scroll lock). Use `useConfirm`, never
  `window.confirm`/`prompt` — none are left.
- `.shell` is the one content width; `lg:grid-cols-shell` the one content+rail
  split. Don't reintroduce `max-w-6xl` or a literal `340px`.
- **The e2e suite is coupled to visible text, uppercase accessible names, and
  `Panel.tsx`'s `▍` glyph.** Change classes freely; preserve those.
- Card faces and anything rendering markdown must carry **no `aria-label`** —
  it overrides descendant content, so a screen reader reads LaTeX source
  instead of the MathML KaTeX emits.

## Testing conventions

- **Playwright runs `workers: 1` against one shared vault.** Build fixtures
  **inside the test**. A startup seed is global state that silently decides
  other tests' outcomes — `web/e2e/seed_flashcards.py`'s docstring is the
  cautionary tale, and it has bitten twice.
- **A wall of red is usually one failure.** The web server sometimes dies
  partway through a run and everything after fails with
  `ERR_CONNECTION_REFUSED` in a uniform ~3s. Find the *first* failure. Known,
  unfixed.
- **e2e runs with no OS keyring on purpose** — a machine with an unreadable
  keyring is a state real users reach. Do not "fix" a keyring-shaped e2e
  failure by installing a backend there.
- **"Pre-existing failure" is a claim that gets proven, not asserted.**
  Junction `node_modules` into a detached worktree at the branch point and
  reproduce it there. `web/e2e/system.spec.ts:42` is currently pre-existing.

## Version control

`CONTRIBUTING.md` is authoritative. The parts most easily got wrong:

- Conventional commits (`feat(scope): …`). Flat branch names —
  `feature/<name>`, `fix/<name>`, `docs/<name>`; never nested under a version.
- **`main` is protected and PR-only.** Required checks are exactly
  `test / python`, `test / web`, `test / e2e`. Never require `package` — it
  does not run on PRs, so requiring it blocks every PR permanently.
- **Never add an install step to a workflow.** Dependencies go in
  `pyproject.toml`; installs live once in `.github/actions/python-env/`.
  Installing in one workflow and not another is how v0.2.0 shipped a backend
  missing two connector libraries. Cap new dependencies — an uncapped dep plus
  a fresh CI resolution turns a green local suite red with no code change.
- A release bumps **all three** manifests in one commit (`pyproject.toml`,
  `web/package.json`, `desktop/package.json`) and only after `main` is green
  *including* `package`. A published GitHub release can never accept new
  assets, so a packaging bug found at tag time costs a version number.

Design docs go to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, plans
to `docs/superpowers/plans/`. Session narratives are journaled into the vault's
`90-Meta/` via `/log-session`.
