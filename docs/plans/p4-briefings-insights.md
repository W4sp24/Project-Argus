# Phase P4 — Briefings + Insights + Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FRIDAY runs itself daily — a 07:00 briefing written into the daily note (via writer, I1/I2), a real insights page, and hardening: audit log, error boundary, `friday doctor`, README, Playwright e2e.

**Architecture:** `backend/briefing.py` assembles deterministic BriefingData (agenda, due/overdue, yesterday's unfinished daily-note tasks, exam countdowns from exam-tagged due tasks, weak topics from review-queues) and renders markdown; an injectable `composer` (real: one opus one-shot via agent SDK; haiku summarization skipped — inputs are tiny, logged as D-028) polishes prose, falling back to the deterministic render on any failure. `writer.write_briefing` replaces-or-appends the `## Briefing` section in today's daily note (snapshot first). APScheduler `BackgroundScheduler` built by `backend/scheduler.py`, attached ONLY by the module-level `app = create_app(...)` so tests never start threads. Insights = `backend/insights.py` scanning ✅ done-dates + tasks_cache + attempts/exams + gcal. Audit = new `audit` table logging entry-point + **path list only** (never prompt text) from chat/planner/generate.

**Tech Stack:** APScheduler, recharts (already in web), @playwright/test (new dev dep), sqlite3, FastAPI.

## Global Constraints

- I1: only `backend/writer.py` mutates the vault; I2: git snapshot before every apply.
- I3: `99-Private/` + `#no-ai` never read/sent; audit rows store paths only, no content.
- I5: subscription auth — never set ANTHROPIC_API_KEY.
- ruff (line 100) + ESLint clean before each commit; conventional commits on `feat/p4-briefings-insights`.

## Tasks

### T1 — briefing data + render (TDD)
`backend/briefing.py`: `BriefingData` (pydantic: date, events, due_today, overdue, yesterday_unfinished, exam_countdowns `[{title, due, days_left}]`, weak_topics `[str]`); `briefing_data(settings, conn, today) -> BriefingData` (refresh_cache + bucketed_tasks + gcal.list_events + unchecked boxes parsed from `10-Daily/<yesterday>.md` + open tasks with due>=today whose text/tags match exam|quiz|midterm|final + unchecked lines from `15-Courses/*/study/review-queue.md`, capped 5); `render_briefing(data) -> str` (markdown: schedule, focus, overdue, countdowns, weak topics — sections omitted when empty); `compose_briefing(settings, conn, composer=None) -> str` (composer(data)->str, any Exception -> fallback render). Tests: fixture vault; due-today task + overdue task land in right fields; exam task yields days_left; render contains/omits sections; failing composer falls back.

### T2 — writer.write_briefing + scheduler + briefing API (TDD)
`writer.write_briefing(vault_path, markdown) -> str`: snapshot (I2) → create today's daily note if missing → replace existing `## Briefing`..next-H2 section else insert after H1 → returns vault-relative path. `backend/scheduler.py`: `build_scheduler(settings, composer=None) -> BackgroundScheduler` — cron 07:00 briefing (compose→write), cron 03:00 `refresh_cache`; jobs wrapped in try/log. `main.create_app(..., scheduler_factory=None)`: when given, lifespan starts/stops it; module-level `app = create_app(scheduler_factory=build_scheduler)`. `backend/briefing_api.py`: `POST /api/briefing/run` (composes with injectable composer via create_app param `briefing_composer`, writes, returns `{path, markdown}`), `GET /api/briefing` (today's `## Briefing` section text or 404). Tests: write twice → one section, git log +2; POST run with fake composer → daily note contains it; GET roundtrip; no scheduler in tests.

### T3 — insights backend (TDD)
`backend/insights.py` + `GET /api/insights` (in briefing_api or own router): `{completion_trend: [{date, completed}] (14d, ✅-date scan of vault md excluding EXCLUDED_TOP_DIRS), overdue: [{date, count}] (open tasks by due date, last 14d), calendar: [{date, event_hours, focus_hours}] (7d back, gcal timed events, focus = max(0, 8 - event_hours), zeros when unconfigured), study: {streak_days, courses: [{course, attempts: [{date, pct}]}]}, configured: {gcal}}`. Streak = consecutive days ending today with a ✅ completion or an attempt. Tests: seeded vault + db give exact numbers; unconfigured gcal → zeros.

### T4 — insights page (recharts) + Today briefing card
Replace `web/app/(dashboard)/insights/page.tsx` placeholders: completion trend line/bar (14d), overdue bar, calendar load vs focus stacked/dual bar (hidden behind `configured.gcal` empty-state), per-course exam-score line + streak stat tile. Keep dev-activity section. SWR on `/api/insights`; dataviz conventions (existing BAR_COLOR pattern). Today page: "Briefing" glass card fetching `GET /api/briefing` (markdown rendered simply; hidden/empty-state when 404) with a "Generate" button posting `/api/briefing/run`. `npm run build` green.

### T5 — audit log
db.py SCHEMA += `audit` table (id, created_at, entry_point TEXT, model TEXT, paths_json TEXT). `backend/audit.py`: `log_prompt(conn, entry_point, model, paths)`, `recent(conn, limit=100) -> list[AuditEntry]`. Wire: `agent/runtime.py` chat (paths of chunks returned by search tool per query), `agent/planner.py` (preferences note + review-queue paths), `agent/generate.py` (material paths — pass through from study callers). `GET /api/audit` (briefing_api or main). Tests: planner context logs row; endpoint returns rows; stored row contains no prompt text (assert payload is path list).

### T6 — friday doctor + error boundary + README
`cli.py` `doctor` subcommand → `backend/doctor.py`: checks vault (exists, is git repo), db (connect+init_schema), chroma (import chromadb + dir writable — WARN if `[rag]` absent), keyring (set/get/delete probe), gcal (configured? OK/WARN), todoist (WARN), each `OK|WARN|FAIL name — detail`; exit 1 only on FAIL. `web/app/error.tsx` + `web/app/global-error.tsx` (glass card, retry button). README: features through P4, quickstart (+`friday connect`, `/plan`, briefing, doctor), screenshots section stub. Tests: doctor on tmp inited vault → exit 0; broken vault path → FAIL.

### T7 — Playwright e2e roundtrip
Web dev deps `@playwright/test`; `web/playwright.config.ts`: globalSetup creates throwaway vault (`friday init` into scratch dir + `.env` in `e2e/.workdir`) and seeds one pending task suggestion via venv python; webServer[0] uvicorn (cwd e2e workdir, port 8000), webServer[1] `next dev` (port 3000). `web/e2e/roundtrip.spec.ts`: /today → capture "e2e roundtrip note" → assert `00-Inbox/capture-*.md` contains it (node fs); /review → Approve seeded task suggestion → assert target note line edited + vault git log has pre-apply snapshot. `npx playwright test` green.

### T8 — exit criteria
pytest -q green && npx playwright test green. Manual: `POST /api/briefing/run` (real composer or fallback) → `## Briefing` in Scientia daily note + Today card renders it; `friday doctor` all OK/WARN. /code-review (manual diff fallback per D-007); fix CONFIRMED. Update BUILD_STATE/BUILD_LOG (D-027..), merge to main, push, journal.
