# FRIDAY — your second brain, on your machine

A local, Jarvis-style assistant built on an [Obsidian](https://obsidian.md) vault:
chat grounded in your own notes, a unified task + calendar day view, a coursework
engine that turns your real lecture materials into cited practice exams, an AI day
planner that only ever *proposes* (you approve every change), and a morning briefing
written into your daily note at 07:00. Your vault stays plain markdown you own —
delete FRIDAY tomorrow and your second brain stays.

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

- **Chat** grounded in your vault — every answer carries citations that deep-link
  back into Obsidian; "that's not in your notes" instead of hallucinations.
- **Today** — live agenda (vault tasks + Google Calendar + Todoist), quick capture
  to `00-Inbox/`, and the morning **briefing** (07:00 job or on-demand): schedule,
  due/overdue, yesterday's leftovers, exam countdowns, weak topics.
- **Planner** — type `/plan tomorrow` in chat; the agent proposes schedule blocks,
  task edits, and note edits into a **Review** queue. Approve applies them through
  a single audited writer (with a git snapshot of the vault first); dismissing with
  a reason teaches the planner your preferences.
- **Study** — upload lecture PDFs/slides, generate study guides and practice exams
  where every question cites a real page, take them in quiz mode, and let missed
  topics feed your review queue (and your briefing, and your planner).
- **Tasks** — Obsidian Tasks syntax parsed vault-wide into overdue/today/week/someday.
- **Insights** — task completion trend, overdue chart, calendar load vs focus time,
  study streak, practice-exam scores per course, plus your coding-session activity.
- **Audit** — `/api/audit` lists exactly which vault files each agent prompt read
  (paths only, never content).

## Quickstart

Prerequisites: Python 3.12+, Node 18+, git.

```bash
git clone <this-repo> && cd <repo>

# 1. Backend
python -m venv .venv
.venv/Scripts/activate          # Windows · use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"

# 2. Create (or point at) your vault
friday init ./my-vault          # new vault from the template, or set VAULT_PATH in .env
                                 # to an existing Obsidian vault

# 3. Run
uvicorn backend.main:app --port 8000      # API on :8000
cd web && npm install && npm run dev       # dashboard on :3000
```

Open <http://localhost:3000>, then check the install:

```bash
friday doctor                   # vault, git, db, chroma, keyring, connectors
```

Optional extras:

```bash
pip install -e ".[rag]"         # chat/RAG stack (embeddings, chroma, pdf extraction)
friday reindex                  # build the search index over your vault
friday connect gcal             # Google Calendar (needs a Desktop OAuth credentials.json)
friday connect todoist <token>  # Todoist personal API token
```

## Project layout

```
backend/        FastAPI app, CLI, storage (Python 3.12, type-hinted, ruff)
web/            Next.js 14 dashboard (TypeScript, Tailwind)
vault-template/ PARA-style vault skeleton copied by `friday init`
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

```bash
pytest                 # backend tests
ruff check backend tests && ruff format --check backend tests
cd web && npm run lint && npm run build
```

Conventional commits (`feat(scope): …`). See `docs/BUILD_STATE.md` for the current
roadmap position and `docs/BUILD_LOG.md` for decisions.

## License

MIT
