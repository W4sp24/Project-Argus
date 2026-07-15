# Argus Terminal HUD — Implementation Guide for Claude Code

Target repo: `W4sp24/Project-Argus` (`web/` — Next.js 14 + Tailwind + TypeScript).
Reference prototype: `Argus Terminal HUD v3.dc.html` (**APPROVED v1** — violet Terminal HUD, option 2a + v2 + v3 revisions).

> **v3 revisions (approved):** heatmap-first General layout (full-width panel directly
> under the stat tiles), Todoist-style task widget (OVERDUE/TODAY/UPCOMING/DONE groups,
> round priority-colored checkboxes, P1–P3 flags, #project labels, inline quick-add),
> token tracker with SESSION / WEEK / ALL views + line charts (all mini charts are
> line graphs now, not bars), MCP.SERVERS panel in System (mcp-obsidian wired +
> mcp-gmail placeholder, read-only scopes), NotebookLM-style COURSE.HUB page per course
> (sources rail with per-source RAG toggles → chat workspace with model selector →
> studio rail), and PROJECTS.VAULT grid in Code (Obsidian frontmatter cards).
>
> **v2 revisions incorporated:** readable two-font system (Inter body / mono labels),
> larger type scale, readable planner timeline, in-web ingestion (files + manual email
> capture), dedicated Flashcards & Practice Exam pages, fullscreen standard-chatbot chat
> with model selector + local model registration, SYSTEM tab (setup guide, doctor,
> integrations incl. gcal/todoist placeholders, Claude Code hook monitor), token-usage
> widgets, quick add-note modal, and CRUD on Study/Research data.

**Prime directive from the user: performance > aesthetics.** No backdrop-filter, no
perpetual animations except trivial opacity cursor blinks, transform/opacity-only
transitions, single font family. The current codebase already removed idle animations
for GPU reasons (see comments in `Sidebar.tsx` / `globals.css`) — this redesign goes
further and removes glassmorphism blur entirely.

---

## 1. Design tokens

Replace the theme in `web/tailwind.config.ts`:

```ts
colors: {
  void:   "#06040c",   // page background
  panel:  "#0c0916",   // card surface
  sunken: "#06040c",   // inputs / nested surfaces (same as void)
  line:   "#1e1733",   // all borders
  lineHi: "#2c2250",   // hovered/active borders
  ink:    { DEFAULT: "#d6cdf0", bright: "#ece7fb", muted: "#9d8fc7", faint: "#5a4f82" },
  ok:     "#34d399",
  danger: "#fb7185",
  // mode accents (CSS var driven, see §2)
  mode:   { general: "#a78bfa", study: "#22d3ee", research: "#e879f9", code: "#34d399" },
},
fontFamily: { mono: ["var(--font-mono)", "monospace"] },  // JetBrains Mono ONLY
borderRadius: {},  // no rounded corners anywhere — square terminal panels
```

Typography rules (v2 — readability first, theme second):
- TWO fonts: **Inter for all body/reading text**, **JetBrains Mono only for terminal
  chrome** (eyebrows, stat labels/values, timestamps, statuses, buttons, `//` lines).
  Drop Space Grotesk. `fontFamily: { body: Inter, mono: JetBrains Mono }`.
- Base body text 14px; list rows 13–13.5px; panel prose `leading-relaxed` (1.6–1.7).
- Eyebrow labels: `▍LABEL.NAME` mono 10px, letter-spacing .18em, accent color.
- Stat labels: mono 9.5px uppercase .16em faint; stat values mono 24px/600.
- Greeting: mono 23px/600. Chat (full page): 14.5px Inter, line-height 1.7.
- Text colors brightened vs v1: body #d9d2f0, muted #a89ecf, faint #7a6fa8 —
  faint is decorative/metadata only, never for interactive labels.
- Rule of thumb: if the user reads it as a sentence, it's Inter and ≥13px;
  if it's chrome/telemetry, it's mono and can be small. When theme aesthetics and
  readability conflict, readability wins.

Remove from `globals.css`: `.aurora`, `.glass` (backdrop-blur), `.gradient-text`,
radial background-image on body. Keep `@keyframes msg-in` idea, add:

```css
@keyframes rise  { from { opacity:0; transform:translateY(8px) } to { opacity:1; transform:none } }
@keyframes blink { 0%,55% { opacity:1 } 56%,100% { opacity:0 } }
```

`prefers-reduced-motion`: disable both + all typing effects (render final text immediately).

## 2. Mode system (the core new concept)

Four modes replace per-page theming: `general | study | research | code`.
A mode changes (a) the accent color of ALL chrome, (b) which panels render.

**State:** React context `ModeProvider` in `web/app/(dashboard)/layout.tsx`:

```ts
type Mode = "general" | "study" | "research" | "code" | "system";
const ACCENTS: Record<Mode, { ac: string; acBg: string }> = {
  general:  { ac: "#a78bfa", acBg: "#171029" },
  study:    { ac: "#22d3ee", acBg: "#0c1a20" },
  research: { ac: "#e879f9", acBg: "#210f20" },
  code:     { ac: "#34d399", acBg: "#0b1712" },
  system:   { ac: "#fbbf24", acBg: "#201804" },
};
```

**Theming mechanism — CSS custom properties, not Tailwind class swaps:**
the provider sets `--ac` and `--ac-bg` on a wrapper div; every accent-colored
element uses `text-[var(--ac)]` / `bg-[var(--ac)]` / `shadow-[inset_0_-2px_0_var(--ac)]`.
One style recalc per mode switch, zero re-render of unaffected components.

**Persistence:** `localStorage["argus-mode"]`, read in a `useEffect` (SSR-safe), and
mirrored to `?mode=` is NOT needed — modes are a client concern.

**Routing:** keep existing pages/routes as-is initially. The mode bar is additive:
- `general` → current Dashboard content (re-skinned) at `/dashboard`
- `study` → `/study` (overview) + **dedicated sub-pages** `/study/flashcards` and
  `/study/exam` — these need their own routes (deep-linkable, back-button friendly);
  the in-mode sub-nav (`OVERVIEW | FLASHCARDS | PRACTICE EXAM`) is a styled tab row
  that `router.push`es between them
- `research` → new route `/research`
- `code` → new route `/code`
- `system` → new route `/system` (setup + integrations, §12)
- `chat` → keep `/chat` as the fullscreen chat page (§7)
Mode tab click = `router.push` + context set. Sidebar is REPLACED by the top bar (§3);
old per-page nav (Tasks/Journal/Review/Insights) moves into General-mode panels and
the command palette. Keep the old routes alive (they're linked from stat tiles).

**Mode-switch transition (cheap):** each panel has `animation: rise .3s ease-out both`
with staggered `animation-delay` (0.04s increments, set inline per panel index).
Because panels unmount/mount on route change, the animation replays naturally.
Plus: greeting line re-types (§5) and a toast `mode :: STUDY loaded`.

## 3. Top bar (replaces Sidebar.tsx)

`web/components/TopBar.tsx` — sticky, `bg-void`, bottom border `line`, z-30:

1. Logo: 14px circle outlined in `var(--ac)` with solid dot inside; `ARGUS_` wordmark,
   the underscore in accent (this is the "cursor" motif).
2. Mode tabs: bordered segmented group, square. Active tab: `bg-[var(--ac-bg)]
   text-[var(--ac)] shadow-[inset_0_-2px_0_var(--ac)]`. Inactive: `text-ink-faint`.
3. Right cluster: `+ NOTE` (opens quick-note modal, §13), `◔ FOCUS` chip (countdown
   when running), `[⌘K]`, `CHAT` toggle, `● LOCAL` (green, static — the privacy
   promise), live clock `HH:MM:SS` (one 1 s interval in TopBar only; do NOT put the
   clock in context or every consumer re-renders).

Mobile: tabs collapse to icons-only; right cluster keeps ⌘K + clock.

## 4. Screen composition per mode

All modes share: status line (`// SYS.{MODE} :: {date} :: vault OK · index OK · agent idle`
— derive from `/api/doctor`-style health endpoint or hardcode until wired), typed greeting,
5 stat tiles, then a `grid-cols-[minmax(0,1fr)_340px]` two-column cockpit.

### General (`/dashboard`, existing data — full re-skin)
- Stats: due today / overdue / done / streak / **tokens** (existing `useInsights` +
  `/api/agenda` logic from `StatTiles.tsx` — keep the data code verbatim, restyle).
- Left: **PLANNER.TIMELINE** — readable v2 layout: 64px right-aligned time gutter
  (start bold 12px / end faint 10px, both mono), block card with 3px accent left
  border + tinted bg, 14px Inter title, kind chip (`DEEP`/`STUDY`/`CODE`/`REST`) and
  duration on the right, and a **now-line** (1px accent rule labeled `now HH:MM:SS`)
  positioned after the last past block. `[Y] APPROVE` / `[N] DISMISS` call the
  existing review API. Header carries a `GCAL: NOT CONNECTED →` chip linking to
  `/system` until the connector is wired (then it shows merged gcal events).
  **TASKS.DUE** (`AgendaCard` logic: keep toggle/edit/delete + optimistic mutate;
  checkbox = `[ ]`/`[x]` glyphs; per-row `×` delete; footer notes todoist state;
  **inline add-task input** → POST a new `- [ ] …` line through the writer).
  **INGEST** (§11) replaces bare CAPTURE.
- **ACTIVITY.HEATMAP is the FIRST panel** — full-width (`col-span-full`) directly
  under the stat tiles, before the two-column grid: heatmap SVG (existing
  `Heatmap.tsx`, ramp `#100c1e → #c4b5fd`) left, summary rail right (streak /
  this-week count / best day + less→more legend). Hover label sits inline in the
  panel header. Mode tabs stay visible in the sticky top bar.
- **TASKS (Todoist-style):** group open tasks into OVERDUE / TODAY / UPCOMING / DONE
  sections (mono 9px section headers with counts + hairline); rows = round 17px
  checkbox bordered in priority color (P1 `#fb7185`, P2 `#fbbf24`, P3 faint; done =
  filled ok-green with ✓), 13.5px Inter task text, P-flag, `#project` label, due
  (accent when today, danger when overdue), hover-visible `×`. Quick-add = bordered
  input row with accent `＋` prefix and natural-language hint
  (`review PR p1 #argus tomorrow` → parse priority/project/date like Todoist).
  Header chip `TODOIST: NOT CONNECTED →` links to /system.
- Right: **ARGUS.AGENT** (briefing summary + actions), **TOKENS.CLAUDE** (§14),
  **ACTIVITY.FEED** (existing), **INSIGHTS.14D** (line chart, §14 chart spec).

### Study (`/study` + sub-pages)
- Stats: courses / next exam T-n / cards due / streak / weak topics.
- **Overview** (`/study`): **COURSES** — full CRUD: `+ ADD COURSE` inline form
  (code + name → creates `15-Courses/<CODE>/`), per-course `+ FILES` upload button,
  `×` delete (removes the course entry, never deletes vault files — say so in the
  toast), weak-topic chips; a shared ingest dropzone below (§11). Right rail:
  FLASHCARDS teaser (`OPEN DECK →`), PRACTICE.EXAM teaser (`TAKE EXAM →`),
  REVIEW.QUEUE (existing weak-topics data).
- **Flashcards page** (`/study/flashcards`, `[PREVIEW]` flag): left = DECK.MANAGE
  (add card form: front/back inputs; card list rows with `×` delete); right =
  STUDY.SESSION (flip card + SRS grade bar `AGAIN / HARD / GOOD / EASY` — grading
  advances to next card and toasts the mock schedule). Store decks as markdown in
  `15-Courses/<CODE>/flashcards.md` (one `Q:: A::` pair per line, Anki-importable)
  when implemented for real.
- **Practice exam page** (`/study/exam`): existing exam data; progress bar (q n/10),
  large 17px question, answer buttons with correct=ok-green / wrong=danger-red border
  states, citation chip, PREV/NEXT nav, SCORES.HISTORY sidebar (from insights data).
  Grading badge `GRADING: PREVIEW` until wired to `backend/study/grader.py`.

### Research (`/research` — NEW route)
- Stats: papers / queued / reading / notes / highlights (counts derive from state).
- Left: **LIBRARY.QUEUE** `[PREVIEW]` — full CRUD: `+ ADD PAPER` form (title,
  authors·venue), `×` delete, **click the status chip to cycle**
  `QUEUED → READING → DONE` (progress bar snaps 0 / keep / 100), ingest dropzone
  below for paper PDFs. Persist as frontmatter notes under `30-Areas/papers/` —
  one markdown file per paper (`status:`, `progress:` keys) so Obsidian stays the
  source of truth.
- Right: **HIGHLIGHTS.RECENT** — add via inline input (append to
  `30-Areas/papers/inbox.md`), `×` delete per row; **ASK.VAULT** card that opens
  fullscreen chat.

### Code (`/code` — NEW route)
- Stats: sessions/wk / commits / PRs open / **tokens** / streak.
- Left, first: **PROJECTS.VAULT** — 2-col grid of project cards read from the
  Obsidian vault's `40-Projects/*.md` via the obsidian MCP (`OBSIDIAN MCP: WIRED`
  chip in the header). Each card = mono file-path header row + status badge
  (`ACTIVE` accent / `PAUSED` amber / `SHIPPED` green, from frontmatter `status:`),
  title + one-line description, `#tag` chips (frontmatter `tags:`), footer with
  task progress (`- [ ]`/`- [x]` checkbox counts parsed from the note → n/total +
  thin progress bar), backlink count (`⇄ n`), last-modified (`✎ 2h ago`), and an
  accent `→ next:` line (first unchecked task). Card click opens the note in
  Obsidian (`obsidian://` URI). Then **DEV.JOURNAL** (real data: `90-Meta/` via
  `backend/journal.py`), **ACTIVE.WORK** `[PREVIEW]` (mock PR list).
- Right: **TOKENS.CLAUDE** (§14), **COMMITS.14D** line chart (drive from
  `backend/activity.py` coding-session data).

### Course Hub (`/study/course/[code]` — NEW fullscreen route, `[PREVIEW]`)
NotebookLM-style three-pane workspace, opened via `HUB →` on a course row:
- **Header:** `← BACK`, `▍COURSE.HUB · CS301`, course name, PREVIEW badge, and the
  shared model selector dropdown (§7) right-aligned.
- **Left rail (300px, RAG context):** `▍SOURCES · n/m selected` — one row per indexed
  course file: square accent checkbox (toggles whether the file is in the retrieval
  set — pass the enabled source ids as a filter to the RAG query), filename,
  `pages · chunks` meta, `PDF`/`MD` type chip; ingest dropzone at the bottom adds
  files to `15-Courses/<CODE>/`.
- **Center (chat workspace):** same fullscreen-chat layout as `/chat` (§7) but
  scoped: empty state `ask CS301` + 3 course-specific suggestion buttons; assistant
  name row `ARGUS · {model} · CS301`; every answer cites source+page chips
  (`⌗ lecture-08-dp.pdf p.9`); input placeholder shows the grounded-source count.
  Conversation state is per-course, separate from the global chat.
- **Right rail (270px, STUDIO):** action buttons — study guide, flashcard deck,
  practice exam (routes to `/study/exam`), weak topics — each generates from the
  currently-selected sources into the review queue; below, a GENERATED list of
  prior artifacts with dates.

## 5. Interaction & feedback specs (the "cool and attractive" part, kept cheap)

| Interaction | Spec |
|---|---|
| Typed greeting | 34 ms/char interval into state; block cursor `▊` in accent with `blink 1.1s steps(1) infinite`. Re-runs on mode switch. Skip instantly under reduced-motion. |
| Panel entrance | `rise .3s ease-out both`, staggered 40 ms per panel. Only on mount — never looping. |
| Chat thinking | assistant bubble shows `processing_query▊` then the reply types at ~3 chars/24 ms; citation chips appear after typing completes. |
| Task toggle | glyph swap `[ ]`→`[x]` (green) + strikethrough + toast `done :: {task} — vault line updated`. |
| Toast | single fixed bottom-left terminal line `> message`, `rise .2s`, auto-dismiss 3.2 s. One at a time (replace, don't stack). |
| Approve/dismiss | buttons swap to a one-line result (`applied via writer — git snapshot {sha} taken first`). |
| Flashcard flip | `transform: rotateY(180deg)`, `transition .45s`, `preserve-3d`, backface-hidden faces. |
| Hover | border-color change only (`line` → `lineHi`), `transition-colors .15s`. No scale/shadow on hover. |
| Focus ring | `outline: 1px solid var(--ac)` via `:focus-visible`. |

Typing engine: one small `useTypewriter(text, speed)` hook (interval + slice). Guard:
clear interval on unmount and on text change; return full text if reduced-motion.

## 6. Command palette (⌘K) — NEW, `[PREVIEW]`

`web/components/CommandPalette.tsx`, rendered in layout, toggled by global keydown
(`meta/ctrl+K`, Escape closes). Overlay `rgba(3,2,8,.72)` (no blur), panel 520px,
`rise .18s`. Rows: `KIND · label · hint`. Substring filter is fine (no fuzzy lib).
Actions v1: 4 mode switches, generate briefing (`POST /api/briefing/run`),
`/plan tomorrow` (chat pipeline), start focus session, open chat, reindex.
Register actions via a simple exported array — no command framework dependency.

## 7. Chat — drawer + fullscreen page + model selection

**Drawer** (quick access, any mode): fixed right, 360px, `transform:
translateX(105%) → none`, `transition .25s`. Reuse `ChatProvider`/`useChat`.
Header: `▍ARGUS.CHAT · {model}` + **⛶ fullscreen button** (routes to `/chat`) + ✕.
Compact bubbles (user: `--ac-bg` bg + `lineHi` border; assistant: void bg + `line`
border), citations as square accent chips.

**Fullscreen page** (`/chat`) — standard-chatbot layout, one shared conversation
with the drawer:
- Centered 780px column; input bar pinned at the bottom.
- Assistant messages: avatar orb + `ARGUS · {model}` name row + 14.5px Inter text,
  citation chips (`⌗ path.md`) under the message. User messages: right-aligned
  tinted bubble. No boxes around assistant prose.
- Empty state: centered orb, "ask your vault", 3 suggestion buttons that send
  directly.
- Footer status line: `◈ {model} · local index · 99-Private/ and #no-ai never leave
  your machine`.
- `← BACK` returns to the previous mode view.

**Model selector** (header dropdown on `/chat`):
- Registry: `claude-sonnet-4` (API, default), `claude-haiku` (API), plus user-added
  **local models** (`LOCAL` tag). Selection persists (`localStorage["argus-model"]`)
  and is sent as a `model` field on the chat WS/POST.
- `+ add local model` row jumps to `/system` → MODELS panel (single registration UI).
- Backend: extend `backend/config.py` with a `models` list
  (`{name, provider: "anthropic"|"openai-compat", endpoint?, key_ref?}`); local
  models hit an OpenAI-compatible endpoint (ollama `http://localhost:11434/v1`).
  API keys go to the OS keyring, never the vault.

NOTE: current `ChatPanel` uses `scrollIntoView` — replace with
`el.scrollTop = el.scrollHeight` on the scroll container.

## 8. Feature flags — new features ship UI-only

```ts
// web/lib/flags.ts
export const FLAGS = {
  flashcards:   "preview",   // Study deck + SRS grading
  library:      "preview",   // Research reading queue
  focusTimer:   "preview",
  palette:      "enabled",   // pure client UI, safe to enable
  activeWork:   "preview",   // Code PR list (mock data)
  emailCapture: "preview",   // manual email extraction (§11)
  tokenUsage:   "preview",   // until a real usage log exists (§14)
  localModels:  "preview",   // registration UI works; routing to ollama comes later
} as const;
```

Every `preview` panel renders a `PREVIEW` badge (bordered 8px tag, `#3d2f66`/`#8b7bc0`)
and uses hardcoded mock data; interactions may run locally (card flip, timer countdown)
but MUST NOT call any backend write endpoint. Grep guard: no `fetch(` inside
`components/preview/**`.

## 11. Ingestion (files + manual email capture)

**INGEST panel** (General; smaller dropzones on Study overview + Research library):
- Dashed-border dropzone, `dragover` → accent border + tinted bg (border-color +
  background transition only). Click opens a file input (`accept=".pdf,.pptx,.docx,.md,.eml"`).
- On drop/pick: POST multipart to a new `POST /api/ingest` → saves the file under the
  right vault zone (course folder if dropped on a course card, else `00-Inbox/files/`),
  then runs the existing extract → chunk → embed pipeline (`backend/rag/`).
- Feedback is a typed status line: `ingesting {name} :: extract → chunk (n) → embed
  (local)` → `done :: {name} indexed · n chunks`. Reuse the typewriter hook.
- Always show the trust line: "files are indexed locally — nothing leaves your machine".

**EMAIL.CAPTURE** `[PREVIEW]` — manual by design (user explicitly wants NO inbox
access). UI: textarea ("paste an email…") + `EXTRACT →` button inside the INGEST
panel; `.eml` files also accepted by the dropzone. Recommended implementation paths,
in order of effort:
1. **Paste → parse (ship first):** `POST /api/ingest/email` with raw text; a Claude
   prompt extracts `{tasks[], dates[], contacts[], summary}`; results become
   *proposals in the existing Review queue* (never direct writes) and the source
   email is archived to `00-Inbox/emails/YYYY-MM-DD-<slug>.md`.
2. **.eml drag-drop:** same pipeline; parse MIME with Python's `email` stdlib
   (headers give clean date/from/subject metadata for frontmatter).
3. **Bookmarklet / share target (later):** a bookmarklet that POSTs the selected text
   of a webmail page to localhost:8000 — still user-initiated, no OAuth, no polling.
Never implement IMAP polling or Gmail API sync — it violates the manual-by-design
contract; SYSTEM tab lists email capture as status `MANUAL` to make that explicit.

## 12. SYSTEM tab (`/system`) — setup, health, integrations, models

For self-hosters (project is MIT/open source) and for monitoring wiring:
- **SETUP.GUIDE**: checklist rows (label + command chip) mirroring the README
  quickstart: install → `argus init` → `argus web` → `[rag]` extras → optional
  connectors. Completion state derives from doctor results, not hand-set flags.
- **DOCTOR**: 2-col grid of `argus doctor` checks (vault, git, db, chroma, keyring,
  connectors) with `OK`/`WARN`/`FAIL` states + `RUN AGAIN` button →
  `POST /api/doctor` (wrap `backend/doctor.py`).
- **MCP.SERVERS** (own panel, above INTEGRATIONS): one card per MCP server —
  status dot + name + port + status chip, a row of tool-name chips, and a detail
  line. v1 servers: `mcp-obsidian :27124` (`WIRED` — tools `read_note`,
  `search_vault`, `append_review_queue`; writes still go via the review queue) and
  `mcp-gmail :27125` (`NOT CONNECTED` + CONNECT → `argus mcp add gmail`; tools
  `search_mail`, `read_thread`, `extract_tasks`; **read-only scopes by design —
  nothing auto-ingested, extractions land in the review queue**; this is the
  user-initiated complement to §11's manual email capture, never IMAP polling).
- **INTEGRATIONS**: rows with status chips + detail lines:
  claude code hooks (`WIRED` — read last session stamp from `90-Meta/`),
  google calendar (`NOT CONNECTED` + CONNECT button →
  shows the `argus connect gcal` instructions; flips to `WIRED` when
  `backend/connectors/gcal.py` has credentials), todoist (same pattern),
  email capture (`MANUAL` — links to §11 rationale). Obsidian moved to MCP.SERVERS.
- **MODELS**: registered model list (name + `API`/`LOCAL` tag + DEFAULT marker +
  delete for user-added), `+ ADD MODEL` form (name + endpoint) → §7 registry.
- **TOKENS.CLAUDE**: full budget panel (§14).

## 13. Quick add-note modal

`+ NOTE` in the top bar (and palette action). Modal: title input + markdown textarea
(`[[wikilinks]]` + `#tags` hint), `SAVE NOTE` → existing capture writer but with a
title-derived filename `00-Inbox/YYYY-MM-DD-<slug>.md`; toast shows the path.
Escape closes; autofocus title.

## 14. Token usage (General right rail, Code right rail, System)

Panel with a **SESSION / WEEK / ALL segmented view switcher** (mono 9px tabs,
right-aligned in the header — mirrors the Claude/API usage-dashboard pattern):
- **SESSION:** total since session start, `in / out / ≈$cost`, progress vs a 25k
  soft cap, **line chart** of tokens per exchange, per-feature breakdown rows.
- **WEEK:** 7-day total + date range vs weekly budget, line chart of tokens/day
  (axis labels = weekday+date), per-feature totals.
- **ALL:** lifetime total (`since {date} · n days · ≈$cost`) vs monthly budget,
  line chart of tokens/week (`w23 → w29`), all-time per-feature totals.
Chart = single SVG polyline + 12%-opacity area fill under it (accent-colored,
`vector-effect: non-scaling-stroke`, `preserveAspectRatio="none"`) — NOT bars,
NOT recharts. Axis line = space-between mono 9.5px labels under the chart.
Implementation: every backend Claude call already flows through `backend/agent/` —
record `{ts, feature, session_id, input_tokens, output_tokens}` from the Anthropic
response `usage` field into a `token_usage` sqlite table; expose
`GET /api/usage?range=session|week|all`. Cost estimate = static per-model rate
table in config. `[PREVIEW]` with mock data until the logging lands.

## 9. File plan

```
web/lib/mode.tsx                 ModeProvider, useMode, ACCENTS        (new)
web/lib/flags.ts                                                       (new)
web/lib/useTypewriter.ts                                               (new)
web/components/TopBar.tsx        replaces Sidebar.tsx                  (new)
web/components/Panel.tsx         square panel + ▍eyebrow + PREVIEW badge (replaces GlassCard)
web/components/StatRow.tsx       5-tile row, data props                (new)
web/components/CommandPalette.tsx                                      (new)
web/components/ChatDrawer.tsx    wraps existing ChatPanel logic        (new)
web/components/Toast.tsx         + useToast (context, single line)     (new)
web/components/FocusTimer.tsx    chip + popover [PREVIEW]              (new)
web/components/preview/Flashcards.tsx, LibraryQueue.tsx, ActiveWork.tsx (new, mock)
web/components/IngestPanel.tsx   dropzone + email capture (§11)        (new)
web/components/NoteModal.tsx     quick add-note (§13)                  (new)
web/components/TokenUsage.tsx    usage panel (§14)                     (new)
web/components/ModelSelect.tsx   dropdown + registry hook (§7)         (new)
web/app/(dashboard)/research/page.tsx, code/page.tsx, system/page.tsx  (new routes)
web/app/(dashboard)/study/flashcards/page.tsx, study/exam/page.tsx     (new routes)
backend: POST /api/ingest, POST /api/ingest/email, GET /api/usage/today,
         POST /api/doctor, models list in config.py                    (new endpoints)
```

Migration order (each step leaves the app working):
1. Tokens + globals.css purge (visual change only).
2. `Panel` replaces `GlassCard` (same props: label/title/children + drag handlers).
3. TopBar + ModeProvider; delete Sidebar; layout margin `md:ml-64` → top padding.
4. Re-skin General/Study with existing data. 5. New routes + preview panels.
6. Palette, drawer, toasts, timer. 7. e2e updates (`dashboard.spec.ts` selectors).

## 10. Performance budget & rules

- No `backdrop-filter` anywhere (delete `.glass`). Solid hex surfaces only.
- Perpetual animations allowed: cursor `blink` (opacity, 2 nodes max on screen).
- Everything else animates only on mount/interaction; transform+opacity only.
- One clock interval, one timer interval, one typewriter interval max at a time.
- Keep `npm run perf:budget` green; Lighthouse perf ≥ 95 on `/dashboard`.
- Charts: mini charts are single-SVG line graphs (polyline + area fill, §14) —
  one SVG node per chart; recharts only on `/insights`.
- Fonts: JetBrains Mono only (400/500/600/700), `display: swap`.
- Heatmap stays SVG rects (~370 nodes ok); hover via per-rect handler as today.

## 11. Accessibility

- Mode tabs: `role="tablist"`, `aria-selected`; toasts `aria-live="polite"`.
- All glyph buttons (`[ ]`, `✕`, `◔`) keep descriptive `aria-label`s (pattern already
  in AgendaCard — preserve it).
- Contrast: `#5a4f82` on `#06040c` is decorative-only; interactive text uses
  `#9d8fc7` or brighter.
- Full reduced-motion path (§1).
