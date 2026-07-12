# Phase P2 — Tasks + Calendar Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One merged view of the real day — vault tasks (Obsidian Tasks syntax) + Google Calendar + Todoist — plus working quick capture through the single writer.

**Architecture:** `backend/writer.py` becomes the ONLY module that writes into user zones of the vault (I1), git-committing before each apply (I2). `backend/tasks/parser.py` scans vault markdown into a `tasks_cache` table. `backend/connectors/{gcal,todoist}.py` degrade gracefully (return `[]` + `configured: false`) until credentials exist — **expected human stop:** Google OAuth `credentials.json` + Todoist token. `/api/agenda` merges events + due tasks; `/api/tasks` serves bucket views; Today/Tasks pages go live.

## Tasks

### T1 — writer.py (I1/I2)
`append_capture(vault, text) -> rel_path`: git-commit vault (pre-apply snapshot), append `- [ ] <text> ➕ <date>` to `00-Inbox/capture-<date>.md` (create with frontmatter). Private `_git_snapshot(vault, reason)`. Tests: capture appends + file created; vault git log grows by 1 before write; **single-writer proof:** source scan — no module outside `backend/writer.py` and `backend/study/` references `00-Inbox` or opens vault files for append/write.

### T2 — tasks/parser.py + cache
Parse `- [ ]`/`- [x]` lines: Tasks-plugin emoji markers (📅 due, ⏳ scheduled, ✅ done-date, ⏫/🔼/🔽 priority), bracket fallbacks (`[due: …]`, `[prio: high]`), `#tags`, source path+line. `refresh_cache(conn, vault)` wipes+refills `tasks_cache` (skips I3-excluded dirs incl. 90-Meta/99-Private). Buckets: overdue/today/week/someday(+done excluded). Tests: fixtures for due/priority/done/tags/scheduled; bucket boundaries.

### T3 — connectors
`gcal.py`: `configured()` (credentials.json + cached token), `list_events(start, end)` via google-api-python-client; token stored via keyring; `connect()` runs InstalledAppFlow (called from CLI `friday connect gcal` — needs the human browser step). `todoist.py`: token from keyring (`friday connect todoist <token>`), `list_tasks()` mapped to the same TaskItem shape. Both return `[]` when unconfigured. Tests: mocked service objects; unconfigured → [].

### T4 — API
`GET /api/agenda?date=` → `{events: [...], tasks: [...], configured: {gcal, todoist}}` (events from connectors, tasks due/scheduled that day from cache; refresh cache on call, cheap). `GET /api/tasks?bucket=` → bucketed board payload. `POST /api/capture {text}` → writer.append_capture. Tests: agenda merge with monkeypatched connectors; capture routes through writer (spy) only.

### T5 — web/today + web/tasks
Today: timeline card (events sorted by start, task chips), top-3 tasks (overdue first, then priority), quick-capture box wired to /api/capture with optimistic toast. Tasks: 4-column bucket board (overdue/today/week/someday), checkboxes display-only for now (edits arrive with P3 approvals), tag pills, due badges. Connector setup hints when `configured` is false.

### T6 — exit criteria
pytest (parser fixtures, agenda merge mocked, capture-through-writer proof); manual: task in Scientia appears in Today view; capture lands in 00-Inbox with a vault git commit. gcal/todoist live check marked BLOCKED on credentials. Merge, push, journal.
