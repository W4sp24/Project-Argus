# FRIDAY — Build State

> Machine-readable position tracker. Updated at every task boundary.
> Resume protocol: read this file, `BUILD_LOG.md`, and the active phase prompt in
> `docs/FRIDAY-Autonomous-Build-Playbook.pdf` §5 (or the P0.5 addendum), re-run the
> test suite, continue from **Next action**.

| Field | Value |
|---|---|
| Active phase | — (roadmap P0–P4 complete) |
| Status | ALL PHASES DONE — maintenance/backlog mode (connector credentials still open) |
| Last green commit | feat/p4-briefings-insights head (72 pytest + 1 Playwright e2e green, web build green) |
| Next action | Optional: Ethan provides gcal/Todoist credentials + runs setup.ps1; `/code-review ultra` follow-up; then backlog features |

## P4 exit criteria evidence (2026-07-12)

```
pytest -> 72 passed, incl.: briefing buckets/exam-countdown/weak-topics; render
  omits empty sections; composer failure falls back deterministically; write_briefing
  replaces (never duplicates) its section + snapshots first (I2); scheduler registers
  07:00+03:00 jobs without starting; production scheduler wired to opus composer
  (regression test from review finding); insights trend/overdue/calendar/streak with
  99-Private excluded (I3); audit roundtrip + paths-only proof + planner logging;
  doctor healthy/broken-vault exit codes
npx playwright test -> 1 passed (22.5s): capture typed into real /today UI ->
  00-Inbox file; Approve clicked on real /review UI -> note line edited, vault git
  gained "friday: pre-apply snapshot (apply suggestion ...)", FRIDAY log appended
Manual (real Scientia): run_briefing_job(composer=agent_composer) -> opus-composed
  "## Briefing" in 10-Daily/2026-07-12.md (weak topics from real review queue;
  existing task line + FRIDAY log preserved); GET /api/briefing serves it (810 chars)
friday doctor -> 5 OK + 2 WARN (gcal/todoist await credentials), exit 0
npm run build -> 12 routes; /insights 112 kB (4 charts + tiles), /today 3.07 kB
Review gate: manual diff review (D-031) -> 1 CONFIRMED finding, fixed (D-029)
```

## P3 exit criteria evidence (2026-07-12)

```
pytest -> 65 passed, incl.: apply(schedule) hits injected gcal AND vault git log
  grows by exactly 1 (I2); dismissed reason stored + retrievable + surfaced in
  dismissal_feedback; drifted note diff fails clean (file byte-identical, row
  still pending); double-apply rejected; planner tool handlers insert rows
  without the SDK; /api/plan -> /api/review -> dismiss roundtrip
Manual (real vault Scientia, real agent claude-opus-4-8):
  run_planner("Plan tomorrow") -> 1 schedule suggestion: two 50-min ES101
  Plate-Tectonics study blocks (weak topic, 3/10 exam) + breaks + overdue-task
  block — planner respected assistant-preferences.md and the review queue
  approve task suggestion #2 (reschedule "Test FRIDAY task board" 07-12 -> 07-13)
  -> vault git gained "friday: pre-apply snapshot (apply suggestion #2 (task))"
  BEFORE the edit (8799f8c); daily note line edited; "## FRIDAY log" audit line
  appended to 10-Daily/2026-07-12.md
  approve of the schedule suggestion -> 409 "Google Calendar is not connected"
  (expected: gcal live insert BLOCKED on credentials; row stays pending)
npm run build -> /review 2.29 kB (kind-badged cards, red/green diff view),
  /chat 2.67 kB (/plan intercept)
Seeded: 30-Areas/assistant-preferences.md in vault-template + Scientia
```

## P2 exit criteria evidence (2026-07-12)

```
pytest -> 56 passed, incl.: parser handles due/priority/done/tags/scheduled
  fixtures; agenda merges correctly (mocked connectors); capture appends via
  writer only (source-scan proof test + endpoint spy)
Manual (live backend + real Scientia vault):
  task "- [ ] Test FRIDAY task board 📅 2026-07-12 ⏫ #friday" in the daily
  note -> appears in /api/agenda tasks+top_tasks with parsed due/priority/tags
  POST /api/capture -> 00-Inbox/capture-2026-07-12.md; vault git log gained
  "friday: pre-apply snapshot (quick capture)" BEFORE the write (I2)
npm run build -> /today 2.58 kB, /tasks 1.46 kB
BLOCKED on Ethan (expected stop): Google OAuth credentials.json (then
  `friday connect gcal`) and Todoist API token (`friday connect todoist <tok>`)
  for the live connector halves; code paths degrade gracefully until then.
```

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
| P2 — Tasks + calendar | ✅ DONE (connector live checks await creds) | writer (I1/I2), parser, agenda merge, capture |
| P3 — Planner + approvals | ✅ DONE (gcal live insert awaits creds) | suggestion queue, writer.apply, planner agent, review UI, /plan |
| P4 — Briefings + insights | NEXT | |

## Blockers

| Item | Needed from | Status |
|---|---|---|
| GitHub remote (repo not pre-created; `gh` CLI not installed) | Ethan | ✅ RESOLVED — origin = github.com/W4sp24/Project-Argus; main + phase branches pushed |
| Obsidian Local REST API plugin + API key (P0.5 MCP) | Ethan | OPEN |
| Google OAuth `credentials.json` (P2) | Ethan | OPEN (expected stop) |
| Todoist API token (P2) | Ethan | OPEN (expected stop) |
