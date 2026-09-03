# Notebook — a mode in its own window, and flashcards worth using

Status: approved 2026-09-03. Branch `feature/notebook-flashcards`, cut from
`origin/main` at `df513b5`.

Three reported problems, one branch, because they share a surface: study
generation loses its work when you navigate away, Study is trapped in the one
window the shell allows, and flashcards cannot be reached at all.

## Problem

### P1 — Generation is held in a component local, so navigation loses it

`backend/features/study/jobs.py` opens with an accurate description of a bug it
was written to fix:

> until this module existed it was a bare `await` held open inside the request.
> Navigating away or reloading the tab cancelled nothing: the backend still
> generated the guide and still wrote it into the vault, but the browser that
> asked for it never learned where it landed.

The backend half landed. `router.py:315` and `:350` accept `background: true`
and answer `202 {"job_id": ...}`; `run_guide_job` / `run_exam_job` run on the
job store's daemon thread and record their result against a row.

The frontend never opted in. Both callsites post without the flag:

| Callsite | Call | Progress state |
|---|---|---|
| `web/components/study/CourseHub.tsx:236` | `mutateJSON("/api/study/guide", { course, model, sources })` | `busyAction`, a `useState` in the component |
| `web/components/study/CoursesPanel.tsx:141` | same shape, `generate(kind, course)` | `busyAction`, same |

Two consequences, and the second is the one that reads as a concurrency bug:

1. **Amnesia.** The `fetch` is held open for minutes. Unmounting the route
   discards the only record the UI has. The guide still lands in the vault;
   nothing tells you where.
2. **A false mutex.** Both components gate every action on
   `disabled={busyAction !== null}` — a *single* global busy flag. One running
   guide disables the exam button, the deck button, and the upload button, for
   every course. Nothing in the backend requires this. `router.py:286` is
   explicit that study generation takes no single-flight slot and "contends
   with nothing".

### P2 — The shell forbids a second window

`desktop/main.js:265`:

```js
// Never open a second BrowserWindow; hand real links to the OS browser.
mainWindow.webContents.setWindowOpenHandler(({ url }) => {
  if (/^(https:|obsidian:)/i.test(url)) shell.openExternal(url);
  return { action: "deny" };
});
```

Every popup is denied. There is one `mainWindow` and one route showing at a
time, so reviewing a deck while a guide generates in a course hub is not
expressible.

### P3 — Flashcards have exactly one input, and nothing produces it

`backend/features/flashcards/store.py:138` — the only way a card enters Argus:

```python
def _flashcards_path(vault_path, course, *, taxonomy=None):
    return vault_path / tax.courses / course / "flashcards.md"
```

Grepped across `backend/`, `web/`, `vault-template/`: **nothing writes
`flashcards.md`.** Ingest emits `Q::`/`A::` self-test tails into `.notes.md`
(`features/ingest/notes.py`, `agent/prompts/note_quality.md`), which the deck
generator never reads. So `POST /api/flashcards/decks` 422s
(`no flashcards.md for course …`) unless the file was hand-authored, and the
empty state in `Flashcards.tsx:130` tells the user to go write one.

What else is missing, from the same file:

- No manual card creation, editing, deletion, or reordering. Cards are a JSON
  blob (`flashcard_decks.cards_json`) written once at generation.
- No deck rename, no deck without a course (`flashcard_decks.course` is
  `NOT NULL`).
- One study activity: flip, then one of four FSRS grades.

The FSRS layer underneath is sound — real `fsrs` (PyPI), one review row per
grading event, latest-row-wins. The scheduler is not the problem. Reach is.

`web/e2e/study.spec.ts:28` has been unpassable since the FSRS rewrite: it
asserts mock card content against a `flashcards.md` fixture that exists
nowhere.

### What Quizlet and Gizmo actually do

Read from the live product rather than from marketing copy.

Quizlet's set page presents **one deck and four verbs**: Flashcards, Learn,
Test, then Match/Blocks/Charms as games. Under the card viewer sits a
`Track progress` toggle, and it changes the control bar's meaning:

| Track progress | Controls under the card |
|---|---|
| Off | `←` `1 / 59` `→`, autoplay, shuffle, settings, fullscreen |
| On | `✗` `1 / 59` `✓`, **undo**, shuffle, settings, fullscreen, and a hint bar reading *"Press Space or click on the card to flip"* |

The card itself carries `Get a hint` top-left, audio and a **star** top-right.
Below everything, `Terms in this set (59)` lists every card as a
term-|-definition row with its own star — the browse surface and the edit
surface are the same list. Import accepts pasted text with a chosen
field delimiter (tab, comma, dash) and row delimiter (newline, semicolon).

Gizmo's public documentation deliberately exposes no grading vocabulary at all
— *"You don't need to configure anything. It works automatically"* — with
spacing stretching as a card is answered correctly. Its lesson is about
restraint at the session boundary, not about a mechanic to copy.

Three things carry over, and they are the design:

1. **A deck is a noun; study modes are verbs applied to it.** Argus has one
   verb and no way to author the noun.
2. **Cramming and scheduling are different acts.** Quizlet's ✗/✓ sort is
   session-local and does not touch Learn's spacing. Grading a whole deck to
   skim it must not corrupt a real FSRS schedule.
3. **The list of cards is the editor.** No separate management screen.

## Decisions

Settled with Ethan before design:

- **Pop-out scope: everything under the Notebook tab.** One window containing
  overview, Course Hub, flashcards and all activities, and the practice exam.
  Not one window per sub-page.
- **Full rename with redirects.** Routes, mode key, and label all become
  Notebook; `/study/*` redirects permanently so existing links survive.
- **All four activities ship:** Review (FSRS), Flashcards (browse/sort), Learn
  (adaptive), Match (game).
- **SQLite is primary; the vault is synced explicitly.** Cards become rows.
  Import and export are deliberate acts, not a write per keystroke.

Taken during design:

- **No backend change for P1.** The async path is correct and tested. This is a
  frontend wiring defect and gets a frontend fix.
- **Job ownership moves above the router.** A provider in the dashboard layout,
  not a hook in a page.
- **Recovery is server-derived, not `localStorage`-derived.** On mount the
  provider adopts running jobs from `GET /api/ingest/jobs`. `localStorage`
  would desynchronise the moment a second window exists.
- **Practice Exam is not replaced by a Quizlet-style "Test".** Argus already
  generates cited, graded exams from a real corpus. Quizlet's Test is a weaker
  instrument over the same deck.
- **Match is click-to-pair, not drag-and-drop.** Drag is hostile to Playwright
  and worse on touch, and buys nothing here.
- **`cards_json` is retained, not migrated away.** See "Migration" below.

## Design

### A. `web/lib/jobs.tsx` — the job registry

A `JobsProvider` mounted in `(dashboard)/layout.tsx`, above the router, so no
navigation can unmount it.

State is a set of tracked job ids. Three ways in:

- `track(jobId)` — called by whatever started the job.
- **Adoption on mount** — `GET /api/ingest/jobs`, take every job whose status
  is `queued` or `running`. This is what makes recovery real: it survives a
  reload, a crash, and a second window opening mid-job. Both windows watch the
  same rows off one backend.
- Adoption on window focus, so a window that slept catches up.

Each tracked id is polled with the existing `useIngestJob` hook, whose
`refreshInterval` is already the function form that stops on a terminal status
(`web/lib/api.ts:1145`). Terminal transitions toast once — wherever the user
is — and drop the id.

`components/JobTray.tsx` renders in the top bar: a pill (`⣿ 2 running`) that
expands to per-job kind, stage, elapsed time, and a link to the result. Silent
when nothing runs.

**The false mutex is removed.** `busyAction: string | null` becomes a set keyed
`${kind}-${course}`. A guide, an exam and a deck for one course, and the same
three for another course, all run at once — which is what the backend already
permits.

`CourseHub.tsx` and `CoursesPanel.tsx` post `background: true`, receive
`{ job_id }`, and call `track()`. Neither holds a promise across a navigation
again.

### B. Study → Notebook

**Rename.** Directory `web/app/(dashboard)/study/` → `notebook/`. In
`web/lib/mode.tsx`: the `Mode` union member `"study"` → `"notebook"`, its
`ACCENTS` entry, `MODE_ROUTES.notebook = "/notebook"`, and
`modeFromPathname`'s `/study` test. `TopBar` label. `StudyTabs` →
`NotebookTabs`, its three hrefs, and its `role="tablist"` label. Component
directory `web/components/study/` → `web/components/notebook/`.

`next.config.mjs` gains a `redirects()` returning permanent redirects for
`/study` and `/study/:path*`. Unlike `rewrites()`, this is same-origin and
carries no runtime port, so baking it at build time is correct.

**Pop-out.** One call, from a `POP OUT ↗` control in the Notebook header:

```js
window.open("/notebook?window=standalone", "argus-notebook", "width=1280,height=880")
```

Three environments, one call:

- **Browser (dev, and Playwright)** — a real second browser window. The named
  target means a second click focuses the existing one. Playwright addresses it
  through `page.waitForEvent("popup")`.
- **Electron** — `setWindowOpenHandler` stops denying unconditionally. It
  allows same-origin URLs whose path is under `/notebook`, and keeps today's
  deny-plus-`openExternal` for everything else. The allowed branch returns
  `overrideBrowserWindowOptions` that **re-declare `preload` and
  `additionalArguments: ["--argus-api=…"]`**. Without those the child renderer
  has no `window.__ARGUS__`, and every API call resolves against the Next
  origin instead of the backend port — a failure that appears only in the
  packaged app, because dev is same-origin through the rewrite. The child also
  gets its own `will-navigate` guard and its own window-open handler, so it
  cannot spawn grandchildren unchecked.
- **Window identity** — `main.js` keeps a `notebookWindow` reference and
  focuses it rather than opening a second. Its bounds persist through
  `desktop/lib/config.js`.

**Standalone chrome.** `?window=standalone` is read once on load and written to
**`sessionStorage`**, which is scoped per window: it survives client-side
navigation and reload inside that window and cannot leak into the main one. A
small `useStandalone()` hook reads it. `TopBar` then renders a compact Notebook
bar — title, sub-nav, engine picker, job tray — instead of the six-mode strip,
so the window cannot navigate out of the mode it exists to hold.

**Cross-window awareness** through `BroadcastChannel("argus-windows")`, a
standard API available in both the browser and the Electron renderer. The
standalone window announces itself on open and on `beforeunload`. While it is
live, the main window's tab renders `NOTEBOOK ↗` and re-focuses the window
instead of navigating; on pop-out the main window falls back to `/dashboard`,
so the mode genuinely moves rather than being duplicated. If the channel is
unavailable the tab degrades to plain navigation.

### C. Flashcards

#### Data model

```sql
CREATE TABLE flashcard_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES flashcard_decks(id),
    card_ref    TEXT    NOT NULL,   -- the key flashcard_reviews already joins on
    front       TEXT    NOT NULL,
    back        TEXT    NOT NULL,
    hint        TEXT,
    position    INTEGER NOT NULL,
    starred     INTEGER NOT NULL DEFAULT 0,
    suspended   INTEGER NOT NULL DEFAULT 0,
    source_path TEXT,               -- vault note imported from, if any
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_flashcard_cards_ref ON flashcard_cards(deck_id, card_ref);

CREATE TABLE flashcard_match_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER NOT NULL REFERENCES flashcard_decks(id),
    elapsed_ms INTEGER NOT NULL,
    pairs      INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

`flashcard_decks` gains `description TEXT`, `source TEXT NOT NULL DEFAULT
'generated'` (`manual` | `imported` | `generated`), and `updated_at`. Its
`course` column keeps `NOT NULL` and uses `''` for a deck belonging to no
course, because relaxing a `NOT NULL` in SQLite requires a table rebuild and
`''` is a value the code can read honestly.

#### Migration — review history must survive

`flashcard_reviews.card_id` is TEXT holding `"{deck_id}:{index}"`, generated in
`store.py:161`. The migration exploits that:

- Every existing deck's `cards_json` explodes into `flashcard_cards` rows whose
  `card_ref` is **byte-identical** to the id that deck's reviews already carry.
- **`flashcard_reviews` is not touched.** No column added, no row rewritten, no
  backfill join. Every grading event stays attached to its card.
- Newly created cards get `card_ref = "c" + uuid4().hex`, which cannot collide
  with the `"{int}:{int}"` legacy shape.

The migration runs in `init_schema` beside the existing `ALTER TABLE` block and
is idempotent — it skips any deck that already has rows.

`cards_json` stays on the table, written as `'[]'`. Dropping a column from a
table that a foreign key references means a create/copy/drop/rename rebuild
with `foreign_keys` off. The repo has precedent for that (`_migrate_scan_key`,
`_migrate_widget_key`), but it is real risk for a cosmetic gain: after this
change `store.py` is the column's only reader, and `'[]'` is honest rather than
stale. A comment on the schema records the decision so the column does not read
as live.

#### Getting cards in — four paths, three of them not AI

1. **Manual.** `POST /api/flashcards/decks` with no course required;
   `POST|PATCH|DELETE /decks/{id}/cards[/{ref}]`; `POST /decks/{id}/cards/reorder`.

   **This changes an existing endpoint's meaning.** `POST /decks` today takes
   `{course}` and generates from `flashcards.md`; it now creates a deck and
   creates nothing else. Generation moves to `/decks/generate` (path 4) and
   parsing `flashcards.md` becomes a special case of path 3. `generateFlashcardDeck()`
   in `web/lib/api.ts:898` and both its callsites move with it, and the
   `test_api_generate_deck_missing_flashcards_md_is_422` test is rewritten
   against the import path that now owns that error.
2. **Paste import.** Parsed client-side, imported through a bulk endpoint.
   Field delimiter tab / comma / dash, row delimiter newline / semicolon, with
   a live preview of what will be created — Quizlet's mechanic exactly.
3. **Import from a note.** `POST /decks/{id}/import/note {path}` runs the
   existing `parse_qa_pairs` over **any** vault note. This is the fix for P3's
   core absurdity: ingest has been writing `Q::`/`A::` tails into every
   generated note all along, and nothing could read them. `flashcards.md`
   becomes one note among many rather than the only one.
4. **Generate from sources.** `POST /api/flashcards/decks/generate` over the
   *selected* corpus — the same corpus the guide uses — through the job store
   with `kind: "deck"`. Cards cite their source. The apologetic
   `note="reads flashcards.md · ignores the selection"` in `CourseHub.tsx:313`
   is deleted, because it stops being true.

Plus **export**: `POST /decks/{id}/export` writes `flashcards.md` into the
course folder through the normal writer path, so invariant I1's git snapshot
applies. Round-trips with path 3.

`parse_qa_pairs` and the delimited parser both move to
`features/flashcards/parsing.py`; the FSRS wrapper moves to `scheduler.py`.

#### The four activities

| Route | Loop | FSRS |
|---|---|---|
| `…/[deckId]/review` | The scheduler of record. `Space` flips, `1`–`4` grade, `U` undoes the last grade, `Esc` exits. Each grade button carries its **real** next interval (`GOOD · 4d`). Ends on a summary: reviewed, accuracy, elapsed, next due. | writes |
| `…/[deckId]/cards` | Browse and cram. Large card, click or `Space` to flip, `←`/`→`, shuffle, `n / N`, star toggle (persisted to `flashcard_cards.starred`). Optional `✗`/`✓` sort with a second round over the `✗` pile only. Filter: all / starred / still learning. | **never** |
| `…/[deckId]/learn` | Adaptive, rounds of 7. Per card the question type escalates with in-session mastery: multiple choice (distractors sampled from sibling cards' backs) → typed answer with fuzzy matching and an "I was right" override → flip-confirm. Runs client-side; only the resulting grade is posted. | writes |
| `…/[deckId]/match` | Six pairs on a board, click a term then its definition. A wrong pair flashes and clears. Timer runs; personal best per deck from `flashcard_match_scores`. | **never** |

The Browse/Match exemption is deliberate and is the reason both exist: skimming
a deck before a lecture must not rewrite a schedule built over weeks.

**Grade previews.** `GET /decks/{id}/due` gains a `preview` object per card —
`{again, hard, good, easy}` as human intervals. `fsrs.Scheduler` computes all
four outcomes without committing any, so this is a read. It is what makes the
grade bar informative rather than four unlabelled verbs.

**Learn's outcome mapping.** Correct on the first attempt → `good`; correct
after using the override or a second attempt → `hard`; wrong → `again`. Learn
and Review therefore feed one schedule, which is the point of having both.

**Answer matching** normalises case, collapses whitespace, strips terminal
punctuation and diacritics, then compares. An exact normalised match is
`correct`; a normalised Levenshtein similarity of **≥ 0.85** is `close`, which
is accepted with the difference highlighted and graded `hard`; anything lower
is `wrong`. The "I was right" override promotes `wrong` to `hard`, never to
`good` — a card you had to argue for is not a card you knew. This is pure,
table-driven logic and lives in `web/lib/flashcards/matching.ts`.

**Hints.** `flashcard_cards.hint` is authored as a third, optional field in the
editor row and surfaces as a `GET A HINT` control in the top-left of the card
in Review, Browse and Learn — Quizlet's placement. Revealing a hint in Learn
caps that card's outcome at `hard`. A card with no hint renders no control.

#### Routes

```
/notebook/flashcards                    deck library — list, create, import, delete
/notebook/flashcards/[deckId]           deck detail — the card list, which is the editor
/notebook/flashcards/[deckId]/review    FSRS session
/notebook/flashcards/[deckId]/cards     browse / cram
/notebook/flashcards/[deckId]/learn     adaptive
/notebook/flashcards/[deckId]/match     game
```

Each is deep-linkable and back-button friendly, matching the reasoning already
recorded for `/study/flashcards` and `/study/exam`.

#### File plan

`Flashcards.tsx` is 9.9 KB doing deck management and a session in one file. It
cannot absorb four activities and an editor.

```
backend/features/flashcards/
  store.py       deck + card CRUD, due queue           (rewritten, narrowed)
  scheduler.py   FSRS wrapper, grading, previews       (extracted)
  parsing.py     Q::/A:: and delimited paste parsing   (extracted)
  vault.py       import from note / export to markdown (new)
  generate.py    LLM deck generation over a corpus     (new)
  jobs.py        the generation job body               (new, mirrors study/jobs.py)
  router.py      routes

web/lib/jobs.tsx                    JobsProvider + useJobs
web/lib/flashcards/
  types.ts  hooks.ts                SWR hooks
  matching.ts  distractors.ts  parsing.ts   pure, unit-tested

web/components/notebook/flashcards/
  DeckList.tsx        library, create, delete
  DeckEditor.tsx      the card list — inline edit (front/back/hint), add, reorder, delete
  ImportDialog.tsx    paste import + import-from-note
  CardFace.tsx        one rendered face, shared by all four activities
  ActivityChrome.tsx  session shell: progress, exit, keyboard help
  ReviewSession.tsx  BrowseSession.tsx  LearnSession.tsx  MatchGame.tsx
web/components/JobTray.tsx
```

`CardFace` preserves the accessibility fix recorded on 2026-08-28: faces are
`<div>`s carrying no `aria-label`, because an `aria-label` overrides descendant
content and would have a screen reader read LaTeX source instead of the MathML
KaTeX emits beside its own `aria-hidden` visual tree. Faces stay non-interactive;
the flip control is a separate button.

The `ui/` primitives are used as they stand — `Dialog` for import, `Button`,
`Field`, `ConfirmDialog`/`useConfirm` for deletion. No `window.confirm`. Type
sizes come from the named scale (`text-label`, `text-lead`); no arbitrary px.
No `focus:outline-none`.

## Testing

**pytest.**

- `parsing.py`: multi-line `Q::` continuation, missing halves, every delimiter
  pairing, embedded delimiters, CRLF, empty input.
- Card CRUD, reorder, star, suspend; deck rename and courseless decks.
- **Migration**: build a legacy deck as `cards_json` plus review rows against
  `"{deck_id}:{index}"`, migrate, and assert every card exists as a row *and*
  its FSRS state is unchanged. Idempotency on a second run.
- `scheduler.py`: all four previews are strictly ordered, and the preview for a
  grade equals the state actually produced by committing it.
- `vault.py`: import from an arbitrary note; export round-trips through
  `parse_qa_pairs`.
- `generate.py` / `jobs.py`: success records path and count; a `StudyError`-shaped
  failure is recorded against its own row rather than raised into the void —
  the same contract `study/jobs.py::_fail` holds.
- Router: status codes for every failure path.
- `tests/features/study/test_study_delete.py:58` inserts a deck directly and
  needs updating with the schema.

**Frontend unit — Vitest.** One new dev dependency, one `npm run test:unit`
script, one CI step in the existing web job. `matching.ts` is table-driven logic
over casing, punctuation, whitespace, diacritics and near-misses; driving thirty
variants through a browser would be absurd. Also covers `distractors.ts`
(never returns the correct answer; degrades when a deck has fewer than four
cards) and the paste parser.

**Playwright.** Fixtures built **inside the test**, never seeded at startup —
the suite runs `workers: 1` against one shared vault, and a startup seed is
global state that has broken this suite before.

- Create a deck through the API, add cards, then run each of the four
  activities. Assert Review persists FSRS state and **Browse does not**.
- The pop-out opens and renders the Notebook (`waitForEvent("popup")`).
- **The P1 regression:** start a generation, navigate to another mode, return —
  the job is still tracked and its result arrives.
- `study.spec.ts` becomes `notebook.spec.ts`; its line 28, unpassable since the
  FSRS rewrite, is repaired by real deck creation rather than deleted.
- A `/study/*` URL still lands on the Notebook.

**CI gates, all required green:** `ruff check .`, `pytest -q`,
`smoke_backend.py`, `tsc --noEmit`, `npm run lint`, `npm run test:unit`,
`npm run build`, `check-versions.mjs`, `npm run e2e`. Because `desktop/main.js`
changes, `ci.yml`'s `changes` job sets `packaging=true` and the full Windows
installer builds on the pull request — which is exactly the check worth having
when window handling changes.

**Manual pass, because CI cannot reach it:** stage the desktop shell, pop the
Notebook out of the *packaged* app, and confirm API calls resolve. The
`additionalArguments` inheritance risk is invisible in dev.

## Out of scope

- **A Quizlet-style "Test" mode.** Practice Exam already generates cited,
  graded exams from a real corpus; a deck-only quiz is a weaker instrument.
- **Renaming the other five modes,** or popping them out. The mechanism is
  written so a second mode is a registration, not a redesign, but only Notebook
  is wired here.
- **Audio, images on cards, and text-to-speech.** Quizlet has them; they are a
  separate feature with their own storage question.
- **Sharing or multiplayer.** Contradicts the local-first, single-writer
  invariants.
- **Anki `.apkg` import/export.** Markdown round-tripping covers the vault case;
  the Anki container format is a project of its own.
- **Dropping `cards_json`.** Reasoned above.
