# FRIDAY — Build State

> Machine-readable position tracker. Updated at every task boundary.
> Resume protocol: read this file, `BUILD_LOG.md`, and the active phase prompt in
> `docs/FRIDAY-Autonomous-Build-Playbook.pdf` §5 (or the P0.5 addendum), re-run the
> test suite, continue from **Next action**.

| Field | Value |
|---|---|
| Active phase | P2 — Tasks + calendar merge |
| Status | NOT STARTED (P1.5 complete) |
| Last green commit | feat/p1.5-coursework head (44 pytest green, web build green) |
| Next action | Plan + execute P2 per playbook §5 (task parser, gcal OAuth [HUMAN STOP: credentials.json], todoist, Today/Tasks pages) |

## P1.5 exit criteria evidence (2026-07-12)

```
pytest -> 44 passed, incl.: uncited question dropped (I6); syllabus dates ->
  suggestion rows only (I1); grader writes review-queue.md; upload lands in
  materials/; quiz endpoint hides answers
Manual (real agent + real lecture PDF, CM8 Plate Tectonics):
  10-question exam generated -> exam_id=1, ALL 10 questions cite real pages
  (p.3-p.9 of CM8-Plate-Tectonics.pdf), written to
  15-Courses/ES101/study/exam-2026-07-12-10q.md (+key)
  grade_attempt(3 right / 7 wrong) -> 3/10, weak topics appended to
  15-Courses/ES101/study/review-queue.md as unchecked boxes
npm run build -> /study 3.06 kB, quiz mode UI compiled
```

## P1 exit criteria evidence (2026-07-12)

```
pytest -> 35 passed, incl.: private/no-ai never indexed (I3); pdf chunk carries
  page meta; retrieval returns seeded fact; recency boost orders daily notes;
  wikilink expansion; ws streams >1 delta chunk
friday reindex (Scientia) -> Indexed 4 chunks from 2 files
Manual QA (real agent, subscription auth, model claude-opus-4-8):
  "What folders does my vault use?" -> cited answer [15-Courses/CS000/course.md] [Welcome.md]
  "When is my dentist appointment?" -> "That's not in your notes." + suggestion
npm run build -> 8 routes; streaming chat UI with citation chips -> obsidian:// links
Note: agent cold start ~20s (embedding model) — mitigated with background warm()
  at first ws connection.
```

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
| P1 — RAG + chat | ✅ DONE | local embeddings, hybrid retrieval, streaming cited chat |
| P1.5 — Coursework engine | ✅ DONE | cited exams, guides, quiz+grading, syllabus import |
| P2 — Tasks + calendar | NEXT | gcal OAuth = expected human stop |
| P3 — Planner + approvals | PENDING | |
| P4 — Briefings + insights | PENDING | |

## Blockers

| Item | Needed from | Status |
|---|---|---|
| GitHub remote (repo not pre-created; `gh` CLI not installed) | Ethan | ✅ RESOLVED — origin = github.com/W4sp24/Project-Argus; main + phase branches pushed |
| Obsidian Local REST API plugin + API key (P0.5 MCP) | Ethan | OPEN |
| Google OAuth `credentials.json` (P2) | Ethan | OPEN (expected stop) |
| Todoist API token (P2) | Ethan | OPEN (expected stop) |
