# FRIDAY — your second brain, on your machine

A local, Jarvis-style assistant built on an [Obsidian](https://obsidian.md) vault:
chat grounded in your own notes, a unified task + calendar day view, and a coursework
engine that turns your real lecture materials into study guides and practice exams.
Your vault stays plain markdown you own — delete FRIDAY tomorrow and your second
brain stays.

![status](https://img.shields.io/badge/status-in%20development-8b5cf6)
![python](https://img.shields.io/badge/python-3.12-8b5cf6)
![next.js](https://img.shields.io/badge/next.js-14-8b5cf6)

## How it works

```
Next.js dashboard  ←→  FastAPI backend  ←→  Obsidian vault (markdown, single source of truth)
                          │
                          ├─ RAG index (local embeddings — your notes never leave your machine for indexing)
                          └─ Claude agent (suggest-then-approve: nothing is written without your click)
```

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

Open <http://localhost:3000>.

## Project layout

```
backend/        FastAPI app, CLI, storage (Python 3.12, type-hinted, ruff)
web/            Next.js 14 dashboard (TypeScript, Tailwind)
vault-template/ PARA-style vault skeleton copied by `friday init`
tests/          pytest suite
docs/           specs, build state, decision log, phase plans
```

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
