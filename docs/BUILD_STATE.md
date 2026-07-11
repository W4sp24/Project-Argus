# FRIDAY — Build State

> Machine-readable position tracker. Updated at every task boundary.
> Resume protocol: read this file, `BUILD_LOG.md`, and the active phase prompt in
> `docs/FRIDAY-Autonomous-Build-Playbook.pdf` §5 (or the P0.5 addendum), re-run the
> test suite, continue from **Next action**.

| Field | Value |
|---|---|
| Active phase | P0 — Foundation |
| Status | IN PROGRESS |
| Last green commit | — |
| Next action | Execute P0 plan (`docs/plans/p0-foundation.md`) |

## Phase ledger

| Phase | Status | Notes |
|---|---|---|
| P0 — Foundation | IN PROGRESS | repo scaffold, vault template, health API, web shell |
| P0.5 — Build journal & Obsidian dev loop | PENDING | addendum doc |
| P1 — RAG + chat | PENDING | |
| P1.5 — Coursework engine | PENDING | |
| P2 — Tasks + calendar | PENDING | gcal OAuth = expected human stop |
| P3 — Planner + approvals | PENDING | |
| P4 — Briefings + insights | PENDING | |

## Blockers

| Item | Needed from | Status |
|---|---|---|
| GitHub remote (repo not pre-created; `gh` CLI not installed) | Ethan | OPEN — local commits only until a remote URL is provided |
| Obsidian Local REST API plugin + API key (P0.5 MCP) | Ethan | OPEN |
| Google OAuth `credentials.json` (P2) | Ethan | OPEN (expected stop) |
| Todoist API token (P2) | Ethan | OPEN (expected stop) |
