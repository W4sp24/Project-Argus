# Native Tasks & Calendar — local-first, zero-setup

Status: approved 2026-08-30. Branch `feature/local-calendar-tasks`, cut from
`main` at `b4d2847` (v0.3.1).

## Problem

Argus's todo and calendar features require a setup ritual before they do
anything, and stay restricted afterwards. Three paths exist and none works out
of the box:

| Path | Setup | Writes? |
|---|---|---|
| `backend/connectors/gcal.py` | Google Cloud Console → Desktop OAuth client → download JSON → upload → browser consent → `pip install .[gcal]` | read-only |
| `backend/connectors/todoist.py` | Todoist account → API token | via n8n only |
| n8n templates | run n8n → register → install template → still needs your own Google OAuth client | yes |

Four findings from reading the code:

1. **`CalendarEvent` is defined inside the Google connector**
   (`backend/connectors/gcal.py:48`) and imported by `sources.py`,
   `briefing/service.py` and `tasks/router.py`. The event model *is* the
   Google integration.
2. **No local event store exists.** Events have exactly two producers:
   `gcal.list_events()` and `sources._events_from_timeline()`. With neither
   wired, `PLANNER.TIMELINE`, `BriefingData.events` and Insights'
   `event_hours` are permanently empty — not degraded, empty.
3. **`apply_suggestion` cannot land a schedule block without Google.**
   `backend/vault/writer.py` dispatches `kind="schedule"` to
   `gcal.insert_event`, which raises when unconnected. Approving a planner
   suggestion is broken on a default install.
4. **Tasks are the healthy half** — `backend/vault/tasks.py` reads Obsidian
   Tasks syntax with zero setup — but quick-add understands only `p1|p2|p3`
   and `today|tomorrow|ISO`, there is no recurrence, and every capture lands
   in `00-Inbox/capture-<date>.md` regardless of intent.

The enabling asset: `backend/features/automations/sources.py` is already the
single funnel every consumer calls. A local source slots in there and nothing
else has to learn about it.

## Decisions

- **Direction:** local-first native store + ICS URL subscriptions.
- **Event storage:** SQLite in `<vault>/.argus/argus.db`.
- **Scope:** full feature.
- **Legacy gcal/todoist/n8n:** untouched; the new source merges alongside.

Rejected: shipping an Argus-owned Google OAuth client (unverified-app warning,
100-user cap, or an embedded secret in a local-first app — and it fixes none
of the four findings above).

## Design

### Neutral event model

New `backend/core/events.py` holds `CalendarEvent`. `gcal.py` imports and
re-exports it so all existing `from backend.connectors.gcal import
CalendarEvent` sites keep working unedited. Fields added additively, with
existing defaults preserved (notably `source: str = "gcal"`):
`id`, `calendar_id`, `notes`, `editable`.

### Schema (`backend/core/db.py`)

Two tables appended to `SCHEMA`. `calendar_id` is `TEXT NOT NULL` — SQLite
permits NULLs inside a PRIMARY KEY and never treats two as equal, so a
nullable key column enforces nothing (the `automation_widgets` lesson).
`kind` carries **no CHECK constraint**: altering one needs a full table
rebuild, and `kind` is exactly what a later feature will want to extend.

- `calendars(id PK, name, kind, color, url_ref, url_display,
  refresh_interval_seconds, last_sync_at, last_sync_error, etag, enabled,
  created_at)`
- `calendar_events(calendar_id, id, title, start, end, all_day, location,
  notes, rrule, exdates, updated_at, PRIMARY KEY (calendar_id, id))`
  plus `idx_calendar_events_start`.

### `backend/features/calendar/`

- `store.py` — dict-returning sqlite CRUD; clock-dependent functions take
  `now: Callable[[], datetime] = _utcnow` (mirrors `automations/store.py`).
- `recurrence.py` — `expand(event, start, end)` over `dateutil.rrule.rrulestr`,
  honouring `EXDATE`, bounded by the query window.
- `ics.py` — `fetch(url, etag)` via httpx with `If-None-Match` (304 keeps the
  cache); `parse(text)` via `icalendar`. Scheme allow-list (`http`/`https`)
  so a pasted `file://` cannot read the disk.
- `sync.py` — `sync_calendar(conn, cal_id, *, client=None)`; one transaction,
  replace-all per `calendar_id`; records `last_sync_at` / `last_sync_error`.
- `router.py` — `build_calendar_router(settings, *, client_factory=None)`,
  prefix `/api/calendar`.

Routes: `GET /events?start=&end=`, `POST /events`, `PATCH /events/{id}`,
`DELETE /events/{id}?scope=one|series`, `GET /calendars`,
`POST /subscriptions`, `DELETE /subscriptions/{id}`,
`POST /subscriptions/{id}/sync`, `GET /export.ics`.

**Secrets.** A secret iCal URL *is* a credential — anyone holding it reads the
calendar. It goes to the OS keyring (invariant I4) under ref `calendar:{id}`
via `backend/agent/credentials.py`, never into SQLite; only a redacted
`url_display` is stored. Repo ordering rules apply: probe before persist,
row-then-keyring on create with row rollback on `CredentialError`,
keyring-then-row on delete.

### The merge (`backend/features/automations/sources.py`)

`calendar_events(conn, day)` becomes a **merge** rather than a pick: local and
subscribed events are always included, then the existing widget-or-connector
result is appended exactly as today. `open_tasks` is untouched.
`answered_by()` gains `"local"`; `GET /api/automations/sources` extends
additively.

**Orthogonality is a test, not a claim:** with no local calendar rows, the
merged result is identical to today's, so `test_sources.py`,
`test_migration_handover.py`, briefing and insights stay green unchanged.

### `apply_suggestion` default

`backend/vault/writer.py` keeps its injectable `gcal_insert` parameter, but
when it is `None` the `schedule` branch writes to the local calendar instead
of raising. Finding 3 is fixed as a side effect, not as new scope.

### Task efficiency

- `RECUR_RE` for the Tasks plugin's recurrence marker → `TaskItem.recurrence`.
- `writer.toggle_task_line` rolls a recurring task forward on completion,
  matching Obsidian Tasks, under the existing CAS / `WriterConflict` guard.
- `POST /api/tasks` — create with a chosen destination note.
- `web/lib/taskQuickAdd.ts` extended: times, natural dates (`next friday`),
  `every monday` → recurrence, `!`/`!!`/`!!!` priority aliases. Stays
  frontend-only: it exists to render the live preview chip, and a second
  backend parser would make the emoji vocabulary a wire format between two
  things that never needed to share one.

### Frontend

New `/calendar` route (month grid + day rail, click-to-create, click-to-edit;
drag-to-reschedule deliberately deferred). `PLANNER.TIMELINE` gains a `+ EVENT`
that creates a **local** event and a read-only chip on subscribed ones.
`TASKS.DUE` gains the richer quick-add and a recurrence badge. `ConnectDialog`
gains a `calendar` flow: one `Field` for the URL, a probe step reporting
"found N events", then save. `IntegrationsHub` lists subscriptions with
last-sync and error state.

### Scheduler

`run_calendar_sync_job(settings)` in `backend/scheduler.py`, hourly
`CronTrigger(minute=0)`, id `calendar-sync`, wrapped so it never raises
(matching `run_refresh_job`). Registered only via `build_scheduler`, so test
apps never spawn it.

### Portability

`GET /api/calendar/export.ics` emits local calendars as standard iCalendar.
SQLite events are invisible in Obsidian and do not ride the vault's git
snapshots; export means they are never trapped.

### Dependencies

`icalendar>=6.0,<7` and `python-dateutil>=2.9,<3` — pure Python, **upper
capped** (uncapped deps plus a fresh CI resolve is the known way a green local
suite goes red with no code change), added to `desktop/argus-backend.spec`
`hiddenimports` and covered by the frozen smoke test.

## Known limits

- **ICS is read-only.** An event created in Argus does not appear in Google.
  Surfaced in the UI as a read-only chip rather than silently dropped.
- **SQLite events are invisible in Obsidian** and outside the vault's git
  snapshots. Mitigated by `export.ics`; the DB does sit under `<vault>/.argus/`
  so vault-level backup carries it.
- **Four ways to get a calendar event** now exist. Consolidating means
  deleting `backend/connectors/`, which is a wider blast radius and is
  deliberately out of scope.

## Verification

1. `.venv\Scripts\python.exe -m pytest tests` (background; ~4 min — the global
   interpreter fails at collection on missing `fsrs`).
2. `.venv\Scripts\python.exe -m ruff check .`
3. `cd web; npx tsc --noEmit; npm run lint; npm run build`
4. `cd web; npm run e2e` — the failure count must not grow.
5. `python desktop/tests/smoke_backend.py` — catches the new deps missing from
   `hiddenimports`.
6. Live check against the real Scientia vault: create an event and see it in
   `PLANNER.TIMELINE` and the briefing; paste a Google secret iCal URL; approve
   a planner schedule suggestion with Google disconnected; complete a
   recurring task.
7. Orthogonality proof, asserted as a test: with no local calendar and no
   subscription, `/api/agenda`, `/api/briefing` and `/api/insights` return
   payloads identical to `main`.
