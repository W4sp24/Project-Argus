# FRIDAY — Build State

> Machine-readable position tracker. Updated at every task boundary.
> Resume protocol: read this file, `BUILD_LOG.md`, and the active phase prompt in
> `docs/FRIDAY-Autonomous-Build-Playbook.pdf` §5 (or the P0.5 addendum), re-run the
> test suite, continue from **Next action**.

| Field | Value |
|---|---|
| Active phase | P1 — RAG + chat |
| Status | NOT STARTED (P0.5 complete pending 2 human steps) |
| Last green commit | feat/p0.5-dev-journal head (17 pytest green, web build green, 8 routes) |
| Next action | Plan + execute P1 per playbook §5 (watcher, extract, chunk, embed, retrieve, agent chat) |

## P0.5 exit criteria evidence (2026-07-12)

```
pytest -> 17 passed (incl. no-ai excluded from all journal endpoints; traversal -> 400)
session-end-journal.ps1 (sample payload) -> stub in 90-Meta/sessions/2026/2026-07-12-project-argus.md
  with correct project/cwd/branch/files fields
session-start-context.ps1 (sample payload) -> additionalContext JSON with project note body
npm run build -> 8 routes incl. /journal; Journal lists seeded project + session
curl /api/journal/projects -> project-argus (1 session, 3 open threads)
curl /api/journal/note?path=../Welcome.md -> 400
/log-session flow -> narrative in session note + project note updated; writes confined to 90-Meta/ (vault git log)
BLOCKED on Ethan: (1) run claude-integration/setup.ps1 to wire session hooks into
  ~/.claude/settings.json (classifier blocks Claude self-installing auto-run hooks);
  (2) Obsidian Local REST API plugin + API key -> update the obsidian MCP entry
  (claude mcp list connectivity check deferred until then).
```

## P0 exit criteria evidence (2026-07-12)

```
pytest              -> 12 passed
friday init ./demo-vault -> template folders + .git present; VAULT_PATH written
curl :8000/health   -> {"status":"ok"}
curl :8000/api/notes -> lists notes with titles/folders
npm run build       -> compiled; routes /, /today, /tasks, /chat, /study, /review, /insights
```

## Phase ledger

| Phase | Status | Notes |
|---|---|---|
| P0 — Foundation | ✅ DONE (fa16fc0) | repo scaffold, vault template, health API, web shell |
| P0.5 — Build journal & Obsidian dev loop | ✅ DONE (2 human steps open) | hooks, /log-session, journal API, Journal page |
| P1 — RAG + chat | NEXT | |
| P1.5 — Coursework engine | PENDING | |
| P2 — Tasks + calendar | PENDING | gcal OAuth = expected human stop |
| P3 — Planner + approvals | PENDING | |
| P4 — Briefings + insights | PENDING | |

## Blockers

| Item | Needed from | Status |
|---|---|---|
| GitHub remote (repo not pre-created; `gh` CLI not installed) | Ethan | ✅ RESOLVED — origin = github.com/W4sp24/Project-Argus; main + phase branches pushed |
| Obsidian Local REST API plugin + API key (P0.5 MCP) | Ethan | OPEN |
| Google OAuth `credentials.json` (P2) | Ethan | OPEN (expected stop) |
| Todoist API token (P2) | Ethan | OPEN (expected stop) |
