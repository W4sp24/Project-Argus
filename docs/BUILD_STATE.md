# FRIDAY — Build State

> Machine-readable position tracker. Updated at every task boundary.
> Resume protocol: read this file, `BUILD_LOG.md`, and the active phase prompt in
> `docs/FRIDAY-Autonomous-Build-Playbook.pdf` §5 (or the P0.5 addendum), re-run the
> test suite, continue from **Next action**.

| Field | Value |
|---|---|
| Active phase | P0.5 — Build journal & Obsidian dev loop |
| Status | NOT STARTED (P0 complete, merged to main @ fa16fc0) |
| Last green commit | fa16fc0 (12 pytest green, web build green) |
| Next action | Plan + execute P0.5 per addendum §6: scaffold 90-Meta in Scientia, hooks, journal API, Journal page |

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
| P0.5 — Build journal & Obsidian dev loop | NEXT | addendum doc |
| P1 — RAG + chat | PENDING | |
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
