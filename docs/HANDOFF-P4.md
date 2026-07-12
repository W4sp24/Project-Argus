# HANDOFF — resume Phase P4 mid-flight

> Paste into a fresh Claude Code session (run from repo root):
> **"Read docs/HANDOFF-P4.md and continue Phase P4 from where it stopped. All Master
> Prompt invariants (I1–I6) and the workflow in docs/plans/p4-briefings-insights.md apply."**

## Position (2026-07-12)

- **Branch:** `feat/p4-briefings-insights` (branched off main after P3 merge `98de824`).
- **P0–P3 are DONE and pushed** (see `docs/BUILD_STATE.md`). P3 evidence is in there too.
- **Plan being executed:** `docs/plans/p4-briefings-insights.md` (tasks T1–T8).
- **Toolchain:** run everything with `.venv\Scripts\python -m ...` (system python lacks deps).
  Web: `cd web; npm run build`. Lint: `ruff format backend tests` + `ruff check backend tests`
  before every commit. ESLint runs inside `npm run build`.

## Task status

| Task | Status | Commit |
|---|---|---|
| T1 briefing module (`backend/briefing.py`) | ✅ committed | 3cd144e |
| T2 writer.write_briefing + scheduler + briefing API | ✅ committed | 4e66d32 |
| T3 insights backend (`backend/insights.py`, /api/insights) | ✅ committed | dc5e386 |
| T4 insights charts + Today briefing card | ✅ committed | 417165e |
| T5 audit log (paths only, /api/audit) | ✅ committed | 5f1ec2b + d00252d |
| T6 friday doctor + error boundary + README | ⏳ **IN PROGRESS — see below** | — |
| T7 Playwright e2e roundtrip | not started | — |
| T8 exit criteria, BUILD_STATE/LOG, merge, push, journal | not started | — |

Full suite was green (75+ tests) after T5. Palette for charts validated:
violet #8b5cf6, cyan #0891b2, rose #e11d48 on surface #17092e.

## T6 — exact state (uncommitted working-tree files)

Done, NOT yet verified green (TDD cycle is mid-GREEN-step):
- `tests/test_doctor.py` — written, was RED (module missing) ✔ correct failure observed
- `backend/doctor.py` — implemented (`run_checks(settings) -> list[Check]`; statuses OK/WARN/FAIL;
  missing vault must not create dirs — guarded in `run_checks`)
- `backend/cli.py` — `doctor` subcommand added (prints checks, exit 1 only on FAIL)

**Next actions, in order:**
1. `.venv\Scripts\python -m pytest tests/test_doctor.py -q` → make GREEN (was never run
   after implementation).
2. Still T6: create `web/app/error.tsx` + `web/app/global-error.tsx` (glass-styled retry
   card, match design tokens in `web/tailwind.config.ts`); refresh `README.md`
   (features through P4: /plan, review queue, briefing, doctor, connect commands).
   `npm run build` green → ruff → full pytest → commit T6.
3. T7 per plan: `@playwright/test` dev dep in `web/`, `playwright.config.ts` with
   globalSetup creating a throwaway vault (`friday init`) + seeded pending task
   suggestion; webServers: uvicorn (cwd = e2e workdir so `.env` points at the test
   vault, port 8000) + `next dev` (3000). Spec: capture via /today UI → file in
   00-Inbox asserted via fs; approve seeded suggestion on /review → line edited +
   pre-apply snapshot in vault git log.
4. T8: full pytest + playwright; manual: `POST /api/briefing/run` against Scientia
   (real vault) → `## Briefing` lands in today's daily note + Today card shows it;
   `friday doctor` all OK/WARN. Manual diff review (no coderabbit CLI, D-007).
   Update `docs/BUILD_STATE.md` (P4 evidence block + ledger → DONE, next = none/maintenance)
   and `docs/BUILD_LOG.md` (decisions D-027+: e.g. audit is path-only by design I3;
   composer haiku step skipped — inputs tiny; scheduler only attached to module-level
   app so tests never spawn threads). Conventional commits; merge --no-ff to main;
   push `origin main feat/p4-briefings-insights`; then /log-session (journal P4
   narrative + project note refresh in Scientia 90-Meta, commit vault).

## Standing rules (unchanged)

- I1 writer.py is the only vault/calendar/todoist mutator; I2 git snapshot before apply;
  I3 99-Private/#no-ai never sent (audit stores PATHS only); I4 keyring; I5 subscription
  auth (never set ANTHROPIC_API_KEY); I6 citations.
- Blocked-on-Ethan items (expected, do not wait): gcal credentials.json, Todoist token,
  Obsidian REST API key, claude-integration/setup.ps1 run.
- TDD for all backend work; commit per green task; decisions → BUILD_LOG.md, not questions.
