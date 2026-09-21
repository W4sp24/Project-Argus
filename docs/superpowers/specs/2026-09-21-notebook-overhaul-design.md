# Notebook overhaul — design as built

**Branch:** `feature/notebook-overhaul` · **Date:** 2026-09-21

## Why

The Notebook produced unusable output. The reported symptoms were "notes are
ugly and unbearable" and exam questions that read like "an AI pretending to
ask". Both were real, and neither had the cause they looked like.

The prompts were the *third* problem. Measured against the author's vault:

| Deck | Reached the model | Actually in the file |
|---|---|---|
| `Mathematical Prelimenaries _1_ _2_.pptx` | 2,236 chars, 0 equations | 7,845 chars, 168 equations |
| `Number Representation.pptx` | 882 chars, 0 equations | 2,746 chars, 30 equations |
| ICS26011 (4 PDFs, 133 pages) | ~2,700 chars total | 133 pages of code screenshots |

`rag/extract.py` iterated `slide.shapes`, which silently skips
`mc:AlternateContent` — where PowerPoint stores every equation. Slide 3 of the
preliminaries deck had one visible shape ("Limits") while its XML held the
full ε–δ definition. Slide 14 held Rolle's Theorem, statement *and* proof.

So the model received a table of contents and was told to ground every claim
in it. It complied: the guide said "the supplied text does not contain its
definition" fourteen times, and the exam asked *"What slide sits between the
two Examples slides?"*. **The output was a correct response to a broken
input.**

## What changed, in dependency order

### 1. Extraction (`rag/extractors/`)

- `omml.py` — OMML → LaTeX. No new dependency (lxml is already a transitive).
  NFKC folds the mathematical-italic codepoints OMML emits; a loose combining
  macron becomes `\overline{x}`, matching what the `m:bar` branch emits,
  because one deck writes the same x-bar both ways.
- `pptx_shapes.py` — walks the shape tree's XML rather than the object model:
  `mc:AlternateContent` (Choice preferred, Fallback if that is all there is),
  groups, tables as Markdown, speaker notes marked `[notes]`. Equations are
  inlined *where they sit*, because an equation left in place is the predicate
  of its sentence.
- `docx_body.py` — sections on heading styles, tables read from the body's own
  XML (`document.tables` is a separate list with no interleaving).
- `ocr.py` — image-only PDF pages: render at 200dpi, RapidOCR, then optionally
  a vision model. `rag/` imports `agent/` nowhere, so escalation arrives as an
  injected `describe` callable built in `main.py`.

**Result:** 2,236 → 12,021 chars (168 equations) and 1,289 → 7,030 chars
(19/19 pages) on the two worst files.

Two judgement calls worth keeping:

- **Confidence cannot detect the commonest OCR failure.** RapidOCR reads a
  heading as `Whatareclasses?` and reports 0.99, because it is certain of
  every glyph. `reads_as_merged_words` judges mean token length instead.
- **The vision budget is spent before the call**, so a provider that is down
  costs a 55-page deck two failed calls rather than fifty-five.

### 2. Selection (`rag/select.py`)

`rag/retrieve.py` — the hybrid search this repo ships — was called by no
generation path. All three generators took `index.all_chunks()` in store order
and packed a 60k **prefix**. So `topics` narrowed nothing, and most of a long
course was structurally unreachable.

Now: each topic is a separate retrieval query (one long query matches passages
vaguely about both topics and strongly about neither); without a query the
course is sampled evenly across source files in document order. One
`pack_excerpts`, one `[SOURCE …]` spelling — the three copies had drifted into
two formats while the prompt asked for a third.

### 3. Note shape (`agent/doctypes.py`)

Seven doc types, each owning its sections. The ordering is load-bearing, not
stylistic: `relations.parse_topics` keeps everything *before* the last
`## Topics` and discards the rest, so the old three-way disagreement between
`note_quality.md` ("end every note with a self-test"), `topics.md` ("finally,
add one more section") and each style's "use exactly these sections" could
silently delete a note's self-test — and the flashcards it becomes.
`formatting.note_contract()` is now the only thing that orders sections.

### 4. Question quality (`agent/itemflaws.py`)

NBME item-writing rules stated to the model and checked afterwards; SuperMemo's
rules for cards, in a separate document because a card is judged on
retrievability rather than on gameability.

The rule that matters — recorded as **I7** — is that a question is about the
subject, never about the document. Run against the real
`exam-2026-09-14-10q.md`, the validator rejects all ten questions, every one
for that reason.

### 5. Memory

- The Course Hub restores its own thread. The backend had persisted every
  thread and turn since threads existed; `openThread` was simply never called
  outside `/chat`.
- `compact_history` summarises what falls off the budget instead of dropping
  it, cached on the thread so it costs one call per compaction.
- `core/notebook_memory.py` — what the tutor knows about a student in a
  course, across threads. Mostly derived from signals that already existed and
  were thrown away (`grader.py` has computed `weak_topics` on every attempt
  since exams shipped, into a file nothing reads back).

### 6. Concurrency

Backend: `claim_job` with `BEGIN IMMEDIATE` (the old check-then-create let two
requests both see an empty slot); a `generate` slot group at capacity 3; one
bounded worker pool; `grade_card` transactional (two grades both read the same
FSRS state and the second silently discarded the first); `_prune` no longer
deletes a running job.

Frontend: exam generation through the job store (the last component holding a
multi-minute call in a local); `track()` optimistic so a button is not live
for a poll interval after the click; one attempt per submit; one grade per
answer; sessions that survive a focus revalidation; guarded deck mutations.

## Rejected, and why

- **`_escape_dollars` on every exam field.** The plan called for it; it is
  wrong. `q`, `answer` and `explanation` legitimately carry LaTeX, so escaping
  them prints the dollar signs. Only an *odd* number of delimiters — which
  cannot be balanced maths — is neutralised.
- **Skipping the self-test on FAQ and Cornell notes.** Reasonable on the face
  of it (both are already question-shaped) and wrong: neither parses as
  `Q::`/`A::`, so those types would have seeded no deck at all.
- **Citing the full vault path.** Consistent with the SOURCE marker and
  unreadable — forty repetitions of a 60-character path inside a table.

## Verification

2,166 backend tests, 91 web unit tests, 29 desktop smoke checks, 14 desktop
node tests, `tsc`/lint/build clean. Playwright: 39/39 in `notebook.spec.ts`.

`web/e2e/system.spec.ts:42` fails, and was **proven** pre-existing by running
it in a detached worktree at the branch point (`b99dac1`) with `node_modules`
and `.venv` junctioned in: identical 31s timeout, identical assertion. The
remaining full-run failures are the documented web-server-death cascade —
every one at a uniform 3.2s after the first, and all of them pass when their
spec runs alone.

Beyond the suites, the pipeline was run end to end against a copy of the real
deck, which is what caught the citation-verbosity regression above.
