# Argus P5 — Dashboard-First Revamp: Design

**Date:** 2026-07-13
**Status:** Approved by Ethan (this session)
**Scope:** Performance pass, dashboard-first home (Layout A "Command Center"), chat dock + animation revision, direct vault edit/delete, vault test-artifact cleanup, Playwright QA.

## Context

Argus (P0–P4 complete, spec v0.2 feature-complete) is a local second-brain web app: Next.js 14 (`web/`) + FastAPI (`backend/`) over the real Obsidian vault **Scientia**. Ethan reports the app feels laggy and wants a dashboard-first experience with productivity metrics, a GitHub-style heatmap, an embedded mini chat, and the ability to edit/delete vault content from the UI.

Decisions settled during brainstorming:

| Question | Decision |
|---|---|
| Vault "dummy data" cleanup scope | Test/dev artifacts only — real notes untouched |
| Vault edit/delete model | Direct apply with git snapshot (single writer); suggest-then-approve remains for AI-initiated changes |
| Daily launch method today | `npm run dev` — dev-mode overhead is part of the perceived lag |
| Dashboard structure | New Dashboard **replaces Today** as home; Insights stays as deep-dive tab |
| Dashboard layout | **Layout A "Command Center"**: day column left, persistent right rail with recent activity + always-visible docked chat |
| Overall approach | Evolve in place on the existing Next.js + FastAPI stack (no rewrite, no desktop shell) |

Standing invariants that bind this design: **I1** single writer `backend/writer.py`; **I3** `99-Private/` + `#no-ai` never indexed/sent; **D1** `90-Meta/` is dev-owned (Argus reads it only); I5 subscription auth; I6 citations on every RAG answer.

## 1. Performance — "super smooth"

Measure → fix → guard:

1. **Production launch path.** Daily use must never touch the dev compiler. Add an `argus web` CLI route (or equivalent script) that runs `next build` once (or detects a stale build) and serves via `next start`, alongside the backend. Dev mode stays for development only.
2. **Interaction cost audit (measured, not guessed).** Profile with Playwright MCP + React DevTools profiling builds. Known suspects, in order:
   - Stacked `backdrop-blur` glass surfaces (GPU-expensive; classic janky-button cause). Fix: cap concurrent blur layers, pre-composite the aurora background, prefer opacity/transform-only hover states, `will-change` where justified.
   - Whole-page re-renders on interaction. Fix: memoize heavy panels, localize state, split SWR keys so one mutation doesn't revalidate the world.
   - Perceived latency on writes. Fix: optimistic UI on task check-off, capture, approve/dismiss — instant visual feedback, reconcile on response, roll back on error.
   - Navigation. Fix: `next/link` prefetch on all sidebar routes.
3. **Perf budget guard.** Playwright spec asserting per-route first-load JS budget (~100 kB, matching current best routes) and interaction-to-feedback under ~100 ms for check-off/capture/nav on a production build. Fails CI/test runs on regression.

**Success criterion:** on a production build on this machine, every button/nav interaction gives visible feedback in under ~100 ms, and route changes feel instant.

## 2. Dashboard (home, Layout A)

`/` becomes **Dashboard**, replacing the Today page (route and nav entry renamed; Today's features live on as widgets). Two-column layout (single column stacked on mobile, chat dock collapses to floating button):

**Left column (the day):**
- **Briefing hero** — the morning briefing (existing P4 pipeline), collapsible; collapsed state persists for the day once read.
- **Stat tiles** — due today, overdue, done today, study streak, calendar load (focus hours). Each tile links to its detail page. Data from existing insights endpoints plus small additions.
- **Agenda** — merged calendar + tasks (existing agenda API) with **inline check-off** (optimistic) and **inline edit/delete** (Section 4).
- **Activity heatmap** — 52-week GitHub-style grid. One cell = that day's productivity events: tasks completed (✅ scan) + notes created/edited + study attempts + captures. Hover: per-type breakdown. Filter: single event type. New endpoint `GET /api/insights/heatmap` aggregating vault scan + existing SQLite tables; `99-Private/` excluded (I3). Color ramp validated against the glass surface like the P4 charts.
- **Quick capture** — existing capture box, unchanged behavior.

**Right rail:**
- **Recent activity feed** — latest vault edits, suggestion approvals, exam attempts, briefings written; each row links to its source (note, review card, or study page).
- **Mini chat dock** — Section 3.

Every widget is interactive — check off, capture, edit, ask, click through. Nothing display-only. Insights remains the deep-dive analytics tab; its existing charts are unchanged (heatmap also appears there in a full-width variant).

## 3. Chat dock + animation revision

**One engine, two surfaces.** The existing `/ws/chat` session + history is lifted into a shared provider mounted in the dashboard layout. Surfaces:
- **Dock** — always-visible panel in the Dashboard right rail; on other pages it collapses to a floating button that opens the same conversation as a pop-over.
- **Chat tab** — the existing full page, unchanged in capability.

Same history, same citation chips; switching surfaces mid-conversation preserves the thread. Citations render as clickable `obsidian://` chips in both surfaces (I6).

**Animation revision:** smooth token streaming without layout jumps (reserve space, no reflow-per-token), message entrance fade/slide ≤150 ms, typing indicator while awaiting first token, citation chips animate in after the answer settles. All motion honors `prefers-reduced-motion`. Animations must be transform/opacity-only (ties into Section 1).

## 4. Vault edit & delete (direct, with snapshot)

New user-initiated write paths — still exclusively in `backend/writer.py` (I1):

- `update_note(path, old_content_check, new_content)` — drift-safe: verifies expected current content (same pattern as `apply_suggestion` diffs), fails clean if the note changed.
- `delete_note(path)`
- `update_task_line(path, old_line, new_line)` / `delete_task_line(path, old_line)` — exact-old-line verification, as today.

Every call: git pre-apply snapshot first, then apply, then an audit line under `## Argus log` in the daily note. Server-side refusals: any path in `99-Private/` (I3) or `90-Meta/` (D1), and path-traversal guarded like the journal endpoints.

**UI surfaces:** edit/delete on agenda task rows (dashboard + Tasks page), delete on captured inbox notes, edit/delete on Journal-adjacent user notes. Deletes require an explicit confirm ("moves are one `git revert` away" messaging). AI-initiated changes keep the suggest-then-approve Review flow — this section only covers changes the human makes directly.

**API:** `PATCH /api/notes/{path}`, `DELETE /api/notes/{path}`, `PATCH/DELETE /api/tasks/line` — thin routes over the writer.

## 5. Vault cleanup & structure optimization (one-time, reviewed)

Enumerate → show Ethan → act only on confirmation → via writer with snapshot:

**Test-artifact removal:**
- `15-Courses/CS000/` (QA test course + test uploads)
- Test session stubs in `90-Meta/sessions/` with obviously synthetic ids (e.g. `abc12345-test-session`) — stub sections only; real narratives stay
- Any other candidate found by a sweep for test/sample markers — **presented as a list first, nothing auto-deleted**

**Structure review (folder relevance):** audit every top-level folder against actual use — note counts, last-modified, inbound links. Known findings to resolve with Ethan: stray daily notes at the vault root (`2026-07-06.md`, `2026-07-12.md`) that arguably belong in `10-Daily/` (requires confirming the daily-note path config and Argus's briefing/✅-scan paths still match after any move); empty or never-used template folders. Output: a proposed keep/move/merge list per folder — applied only after Ethan approves, as vault git commits. `90-Meta/` (dev journal, D1) and `99-Private/` (I3) are explicitly kept and untouched.

Real notes, dailies, courses, people, references are never deleted by this step. The whole sweep is one reviewable commit in the vault's git history.

## 6. Error handling

- Writer endpoints return structured errors (drift conflict → 409 with current content so the UI can re-diff; forbidden path → 403; missing → 404). UI surfaces conflicts as "note changed since you opened it — reload?".
- Optimistic UI rolls back on error with a toast; no silent failures.
- Heatmap/stat endpoints degrade to empty states with a retry affordance (matching existing error-boundary patterns from P4 hardening).
- Chat dock reconnects the websocket with backoff; a dropped stream shows a resume affordance instead of a frozen cursor.

## 7. Testing & QA

- **TDD throughout** (pytest backend, Playwright UI).
- New pytest coverage: heatmap aggregation (seeded vault fixture, 99-Private exclusion), writer update/delete paths (drift conflict, forbidden paths, snapshot-first ordering), cleanup enumeration.
- New Playwright e2e: dashboard renders all widgets against a seeded throwaway vault; heatmap cell counts match seeded events; mini-chat roundtrip in dock and tab sharing one history; inline check-off optimistic flow; edit/delete → filesystem + git snapshot assertion (mirroring the existing capture→approve e2e); perf budget spec (Section 1.3).
- Full existing suite (72 pytest + e2e) stays green at every merge.

## 8. Process

- Feature branch (`feat/p5-dashboard-revamp` or split per area if cleaner), conventional commits, push + PR to `main`; commits only at green-test checkpoints.
- **Session logging to the vault journal (`90-Meta/sessions/`) at each milestone** — via Obsidian MCP when the Local REST API is reachable, direct file write fallback otherwise (D1: dev-owned either way). Obsidian REST API is currently down (port 27124 refused) and its key was a known placeholder — Ethan to launch Obsidian/enable the plugin and provide the key.
- Decisions logged in `docs/BUILD_LOG.md` per the existing D-series convention.

## Out of scope

- Desktop shell (Tauri) — possible future phase.
- New AI capabilities (planner/briefing changes beyond surfacing them on the dashboard).
- Credential-gated items (gcal, Todoist) — unchanged; agenda continues to degrade gracefully.
