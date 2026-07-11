# Phase P0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Runnable skeleton — vault template generator (`friday init`), FastAPI backend with `/health` + `/api/notes`, SQLite store, and a Next.js 14 six-page dashboard shell with the FRIDAY design system (purple / gradient / glassmorphism).

**Architecture:** Python package `backend/` (FastAPI + CLI) reads a vault directory defined by `VAULT_PATH` in `.env`; `vault-template/` is a PARA skeleton copied by the CLI, which also git-inits the new vault (I2 groundwork). `web/` is a Next.js 14 App Router app that proxies `/api/*` to `:8000` and renders the nav shell for Today / Tasks / Chat / Study / Review / Insights.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, Pydantic v2, pytest + httpx, python-frontmatter, ruff · Next.js 14, TypeScript, Tailwind, SWR.

## Global Constraints

- Invariants I1–I6 (Playbook §2) apply; P0 touches I1/I2 only via `friday init` git-initing the vault.
- Python 3.12; type hints everywhere; Pydantic models for every API payload; PEP 8 via ruff (line length 100).
- Conventional commits (`feat(scope): ...`); commit after each green task.
- Heavy RAG deps (chromadb, sentence-transformers) are declared in `[project.optional-dependencies].rag`, NOT installed in P0.
- Web: Next.js 14 App Router, TS strict, Tailwind; `/api/:path*` rewrite → `http://127.0.0.1:8000/api/:path*`.
- Design tokens (user requirement D-003): purple base (violet-600 family), gradient accents, glassmorphism cards (`backdrop-blur`, translucent surfaces), dark futuristic base layer.
- CORS: `http://localhost:3000` only.

---

### Task 1: Python project scaffold + config module

**Files:**
- Create: `pyproject.toml`, `backend/__init__.py`, `backend/config.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `backend.config.Settings` (pydantic-settings-free, stdlib dotenv parse): `Settings.load(env_file: Path | None) -> Settings` with fields `vault_path: Path`, `db_path: Path` (defaults `.friday/friday.db` beside vault), `backend_port: int = 8000`.

**Steps:**
- [ ] Failing test: `Settings.load` reads `VAULT_PATH` from an env file and derives `db_path = vault/.friday/friday.db`; missing file → defaults with `vault_path=None` guard raising `ConfigError` on access.
- [ ] Implement `backend/config.py` (small hand-rolled `.env` parser: `KEY=VALUE`, `#` comments, no quotes needed).
- [ ] `pytest -q` green → commit `feat(config): settings loader with .env parsing`.

### Task 2: Vault template + `friday init` CLI

**Files:**
- Create: `vault-template/` tree per spec §3 — `00-Inbox/`, `10-Daily/`, `15-Courses/CS000/{course.md,notes/,materials/,study/}`, `20-Projects/`, `30-Areas/`, `40-People/`, `50-Reference/`, `99-Private/`, `Welcome.md` (+ `.gitkeep` in empty dirs)
- Create: `backend/cli.py` (argparse; `friday init <path>`, `friday --help`), console script `friday = backend.cli:main`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `backend.cli.init_vault(dest: Path, env_file: Path) -> Path` — copies template, `git init`s the vault, writes `VAULT_PATH=<abs dest>` into `env_file`.

**Steps:**
- [ ] Failing tests: init into tmpdir → template folders present, `.git/` exists, `.env` contains `VAULT_PATH`; init into existing non-empty dir → refuses with clear error.
- [ ] Implement `cli.py` using `shutil.copytree` + `subprocess git init` + initial vault commit.
- [ ] Green → commit `feat(cli): friday init vault generator`.

### Task 3: SQLite layer

**Files:**
- Create: `backend/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `backend.db.connect(db_path: Path) -> sqlite3.Connection` (WAL, foreign keys, row factory), `init_schema(conn)` creating `suggestions(id INTEGER PK, created_at TEXT, kind TEXT CHECK(kind IN ('schedule','task','note')), payload_json TEXT, rationale TEXT, status TEXT DEFAULT 'pending', applied_at TEXT)`.

**Steps:**
- [ ] Failing tests: connect creates parent dirs + WAL mode on; schema idempotent; kind CHECK rejects bad kind.
- [ ] Implement; green → commit `feat(db): sqlite store with suggestions table`.

### Task 4: FastAPI app — /health, /api/notes

**Files:**
- Create: `backend/main.py`, `backend/notes.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `create_app(settings: Settings) -> FastAPI`; `GET /health → {"status":"ok"}`; `GET /api/notes → list[NoteInfo]` where `NoteInfo{path: str, title: str, folder: str, modified: str}` — walk vault `*.md`, title = frontmatter `title` | first `# H1` | filename; skip `.obsidian/`, `.friday/`, `.git/`, `99-Private/` (I3 groundwork).
- Consumes: Task 1 `Settings`, Task 2 template for test fixtures.

**Steps:**
- [ ] Failing tests (httpx TestClient + tmp vault fixture): health ok; notes lists seeded note with title; private note excluded.
- [ ] Implement; green → commit `feat(api): health and notes endpoints`.

### Task 5: Next.js 14 web shell + FRIDAY design system

**Files:**
- Create: `web/` via `create-next-app@14` (TS, Tailwind, App Router, no src dir)
- Create: `web/app/(dashboard)/{today,tasks,chat,study,review,insights}/page.tsx`, shared `web/components/{Sidebar.tsx,GlassCard.tsx,PageHeader.tsx}`, `web/lib/api.ts` (SWR fetcher), design tokens in `web/app/globals.css` + `tailwind.config.ts`
- Modify: `web/next.config.mjs` (rewrites `/api/:path*` → `127.0.0.1:8000`)

**Interfaces:**
- Produces: route group layout with fixed glass sidebar nav (6 entries + logo), each page = `PageHeader` + placeholder `GlassCard`s wired to SWR where an endpoint exists (`/api/notes` on Today as proof of plumbing).

**Design directives (D-003):** near-black violet body gradient (`#0a0118 → #1a0b2e`), radial glow accents, glass cards `bg-white/5 border-white/10 backdrop-blur-xl rounded-2xl`, primary gradient `violet-500 → fuchsia-500`, Inter/Geist type, subtle noise-free minimalism — futuristic but restrained.

**Steps:**
- [ ] Scaffold app, add tokens/components/pages.
- [ ] `npm run build` compiles; nav renders 6 pages.
- [ ] Commit `feat(web): dashboard shell with FRIDAY design system`.

### Task 6: README + exit criteria run

**Files:**
- Create: `README.md` (open-source quickstart: clone → `pip install -e .[dev]` → `friday init` → `uvicorn` + `npm dev`), `.env.example`

**Steps:**
- [ ] Run full exit criteria (Playbook §5 P0): `pytest -q`, `friday init ./demo-vault`, `curl :8000/health`, `npm run build`; paste outputs into BUILD_STATE.
- [ ] Update `docs/BUILD_STATE.md` + `docs/BUILD_LOG.md`; commit `docs: P0 exit criteria evidence`.

## Self-review

- Spec coverage: pyproject ✔ (T1) · vault template + init CLI ✔ (T2) · db.py suggestions ✔ (T3) · main.py health/notes ✔ (T4) · web shell 6 pages + proxy ✔ (T5) · exit criteria ✔ (T6). `.gitignore` already committed in repo scaffold.
- Types consistent: `Settings` consumed by T2 (env write path) and T4 (`create_app`).
- No placeholders beyond design directives, which are token-exact.
