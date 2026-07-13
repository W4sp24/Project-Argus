# Argus — your second brain, on your machine

A local, Jarvis-style assistant built on an [Obsidian](https://obsidian.md) vault:
chat grounded in your own notes, a unified task + calendar day view, a coursework
engine that turns your real lecture materials into cited practice exams, an AI day
planner that only ever *proposes* (you approve every change), and a morning briefing
written into your daily note at 07:00. Your vault stays plain markdown you own —
delete Argus tomorrow and your second brain stays.

![status](https://img.shields.io/badge/status-v0.1-8b5cf6)
![python](https://img.shields.io/badge/python-3.12-8b5cf6)
![next.js](https://img.shields.io/badge/next.js-14-8b5cf6)

## How it works

```
Next.js dashboard  ←→  FastAPI backend  ←→  Obsidian vault (markdown, single source of truth)
                          │
                          ├─ RAG index (local embeddings — your notes never leave your machine for indexing)
                          └─ Claude agent (suggest-then-approve: nothing is written without your click)
```

## Features

- **Dashboard** (home) — Layout-A command center: morning briefing (collapsible),
  stat tiles (due/overdue/done/streak/focus hours), an interactive agenda you can
  check off / edit / delete inline, a GitHub-style productivity heatmap (tasks +
  notes + study + captures, 52 weeks), quick capture, a recent-activity feed, and
  a docked mini chat — all on one screen, every widget interactive.
- **Chat** grounded in your vault — every answer carries citations that deep-link
  back into Obsidian; "that's not in your notes" instead of hallucinations. One
  shared conversation whether you use the dashboard dock or the full **Chat** tab.
- **Vault edit & delete** — edit or delete your own notes and task lines straight
  from the UI (agenda rows, captured inbox notes, Journal-adjacent notes). Every
  change goes through the single writer with a git snapshot first (undo is
  `git revert` away) and a compare-and-swap check that fails cleanly if the file
  changed under you. `99-Private/` and `90-Meta/` are always refused, server-side.
  AI-initiated changes still go through the suggest-then-approve **Review** queue —
  direct edit/delete is for changes *you* make yourself.
- **Today's flow** — quick capture to `00-Inbox/` (click, or drag a file straight
  onto a course card on **Study**), and the morning **briefing** (07:00 job or
  on-demand): schedule, due/overdue, yesterday's leftovers, exam countdowns, weak
  topics.
- **Planner** — type `/plan tomorrow` in chat; the agent proposes schedule blocks,
  task edits, and note edits into a **Review** queue. Approve applies them through
  a single audited writer (with a git snapshot of the vault first); dismissing with
  a reason teaches the planner your preferences.
- **Study** — upload or drag-and-drop lecture PDFs/slides, generate study guides and
  practice exams where every question cites a real page, take them in quiz mode, and
  let missed topics feed your review queue (and your briefing, and your planner).
- **Tasks** — Obsidian Tasks syntax parsed vault-wide into overdue/today/week/someday.
- **Insights** — task completion trend, overdue chart, calendar load vs focus time,
  study streak, practice-exam scores per course, the productivity heatmap (full-width),
  plus your coding-session activity.
- **Audit** — `/api/audit` lists exactly which vault files each agent prompt read
  (paths only, never content).

## Quickstart

Prerequisites: Python 3.12+, Node 18+, git. (Windows: PowerShell works fine for all
commands below; there's no `gh`/`uv` dependency for the core app.)

```bash
git clone <this-repo> && cd <repo>

# 1. Backend
python -m venv .venv
.venv/Scripts/activate          # Windows · use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"

# 2. Create (or point at) your vault
argus init ./my-vault           # new vault from the template, or set VAULT_PATH in .env
                                 # to an existing Obsidian vault

# 3. Build the dashboard once
cd web && npm install && cd ..

# 4. Run — daily-use production launcher (builds if needed, serves both together)
argus web                       # dashboard on :3000, API on :8000, Ctrl-C stops both
```

Open <http://localhost:3000> — the Dashboard is now the home page — then verify the
install:

```bash
argus doctor                    # vault, git, db, chroma, keyring, connectors
```

`argus web` rebuilds the dashboard automatically the first time (or whenever
`web/.next` is missing); pass `--build` to force a rebuild after pulling UI changes,
or `--port`/`--backend-port` to change the defaults.

Optional extras:

```bash
pip install -e ".[rag]"         # chat/RAG stack (embeddings, chroma, pdf extraction)
argus reindex                   # build the search index over your vault
argus connect gcal              # Google Calendar (needs a Desktop OAuth credentials.json)
argus connect todoist <token>   # Todoist personal API token
```

Both connectors are optional — everything else works without them, and `argus doctor`
reports them as `WARN` (not `FAIL`) until you connect them.

## Project layout

```
backend/        FastAPI app, CLI, storage (Python 3.12, type-hinted, ruff)
web/            Next.js 14 dashboard (TypeScript, Tailwind)
vault-template/ PARA-style vault skeleton copied by `argus init`
tests/          pytest suite
docs/           specs, build state, decision log, phase plans
```

## Claude Code dev loop (optional)

The `claude-integration/` folder journals your Claude Code sessions into the vault's
`90-Meta/` zone and surfaces them on the dashboard's **Journal** page:

```powershell
powershell -ExecutionPolicy Bypass -File claude-integration\setup.ps1 -VaultPath "C:\path\to\your\vault"
```

That installs two session hooks (objective stubs, written even with Obsidian closed)
and a `/log-session` command (narrative writeups on demand). For vault access from
Claude via MCP, install Obsidian's **Local REST API** community plugin and register
its key: `claude mcp add obsidian -s user -e OBSIDIAN_API_KEY=<key> -e OBSIDIAN_HOST=127.0.0.1 -e OBSIDIAN_PORT=27124 -- uvx mcp-obsidian`.

## Privacy

- Indexing, embeddings, and storage are fully local.
- `99-Private/` and any note tagged `#no-ai` are never indexed and never sent to any model.
- Secrets live in the OS keyring — never in the repo or the vault.

## Development

Daily use is `argus web` (above); for active development, run the backend and
the Next.js dev server separately so you get hot reload:

```bash
uvicorn backend.main:app --port 8000      # API on :8000
cd web && npm run dev                      # dashboard — next dev picks :3000, or the next
                                            # free port if something's already listening
```

```bash
pytest                 # backend tests
ruff check backend tests && ruff format --check backend tests
cd web && npm run lint && npm run build && npm run perf:budget
cd web && npm run e2e  # Playwright end-to-end (dashboard widgets, vault CRUD roundtrip, chat dock)
```

Conventional commits (`feat(scope): …`). See `docs/BUILD_STATE.md` for the current
roadmap position and `docs/BUILD_LOG.md` for decisions.

## License

MIT
