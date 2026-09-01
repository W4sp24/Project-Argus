# Note relationships — generated notes that link into the vault

Status: approved 2026-09-01. Branch `feature/notes-relationships`, cut from
`origin/main` at `4d08f59`.

## Problem

Every note Argus writes lands in the vault as an island. Querying the vault
through Obsidian — graph view, the backlinks panel, tag search — cannot reach
generated content at all, so the material Argus produces is findable only by
full-text search.

Read from the code and from the live Scientia vault:

| Writer | What it emits today | Verdict |
|---|---|---|
| `features/ingest/notes.py::note_markdown` | 7 frontmatter keys, body, one trailing `[[<source stem>]]` | The source is a PDF; the extension is stripped, so the link resolves to nothing. No `tags`, no `related`. |
| `features/study/study_guide.py::generate_study_guide` | `body + "\n"` | No frontmatter at all — no title, type, course, links or tags. |
| `core/taxonomy.py` | Nine zones | `60-Knowledge/` and `85-Templates/` were added to the vault in July 2026; the code has no name for either. |

What the vault offers as link targets, which is what makes this worth doing:

- **23 notes** under `60-Knowledge/{Artificial Intelligence, Computer Science,
  Cybersecurity, Mathematics, General}`, already following a live convention —
  `type: concept`, `domain:`, `related: [[X]]` — plus a hierarchical book set
  (ISLP chapters 01–13).
- `85-Templates/Concept Template.md` defines that shape and is wired as
  Obsidian's templates folder, so an unresolved link creates a correctly
  structured note on click.
- **No Dataview plugin is installed.** Queries run through core search, tags,
  the backlinks panel and graph view. That settles a design question: body
  wikilinks and tags are load-bearing, frontmatter is secondary.

Two enabling assets already exist and are reused rather than duplicated:

1. `vault/links.py::build_link_index()` resolves stem / `folder/Note` /
   frontmatter alias with a documented ambiguity rule, and already excludes
   `99-Private/` and `90-Meta/`. It is the resolver.
2. `rag/retrieve.py` already performs bounded one-hop wikilink expansion, so
   links written into a note body improve Argus's own retrieval as well as
   Obsidian's UI.

## Decisions

- **Link source:** hybrid. The model names the concepts; `build_link_index`
  verifies them against the real vault; retrieval adds neighbours the model
  never saw.
- **Concepts with no note yet:** emit the link anyway. Obsidian renders it as
  an unresolved link, shows it in the graph, and creates it from the Concept
  Template on click.
- **Existing notes:** a `relink` job on the existing job store, plus
  `argus relink`. Guarded to `generated_by: argus`.
- **Scope:** ingest notes (all four styles) and course study guides, through
  one shared module. Practice exams and flashcards deferred.
- **Frontend:** one `RELINK NOTES` action on `/sources`, reusing the reindex
  button and job-readout pattern already there.

Rejected:

- **Physical backlinks written into other notes.** Obsidian derives backlinks
  from forward links natively; mutating hand-written notes is risk with no
  payoff.
- **Auto-created concept stubs.** Every link would resolve, at the cost of
  filling the vault with empty files and polluting retrieval with
  content-free chunks.
- **`Taxonomy.knowledge = "60-Knowledge"`.** Would let a hollow link be
  path-qualified into the right domain folder, but the domain cannot be
  reliably inferred, and it puts risk into a module whose defaults are pinned
  byte-identical by `test_defaults_match_v0_2_constants`. A bare
  `[[Concept]]` lets Obsidian's own new-note rule decide.

## Design

### `backend/vault/relations.py` — the shared module

One purpose: given a note's content and its source path, produce the
relationships it should carry. Takes a resolver and a neighbour-finder as
injected callables and performs no I/O of its own, so it is testable without a
vault or an embedding model.

```python
@dataclass(frozen=True)
class Relation:
    target: str            # "Determinism" | "60-Knowledge/AI/Linear Regression"
    display: str | None    # alias half, present when path-qualified
    kind: Literal["concept", "neighbour", "source", "course"]
    resolved: bool         # did build_link_index find a real file?

@dataclass(frozen=True)
class Relations:
    topics: list[str]      # normalised, feeds frontmatter + tags
    links: list[Relation]  # ordered, deduped, capped
```

Caps: `MAX_TOPICS = 7`, `MAX_NEIGHBOURS = 3`.

### Topics: a prompt tail that degrades to today's behaviour

`build_prompt` gains a tail shared by all four styles and the guide, asking for
a trailing `## Topics` section of 3–7 concept names — "the thing as a textbook
would title it, not a sentence, not a phrase lifted from the document, and not
the document's own title".

`parse_topics(body)` splits that section off the body before it is written, so
topics never appear twice.

**The load-bearing property: if the model ignores the instruction,
`topics == []` and everything else still works.** No error path, no failed
note, just fewer links. Same for a model that writes the section in the wrong
place.

`normalise_topic()` strips emphasis, backticks and trailing punctuation,
collapses whitespace, drops a leading article, title-cases only all-lowercase
input, rejects anything under three characters or purely numeric, and dedupes
case-insensitively. Without it, one concept becomes three graph nodes
(`determinism`, `Determinism`, `**Determinism**`) — which makes the graph
worse, not better.

### Resolution

Each normalised topic goes through `LinkIndex.resolve(topic,
from_path=note_destination)`:

- **Hit** → path-qualified with an alias:
  `[[60-Knowledge/Artificial Intelligence/Linear Regression|Linear Regression]]`.
  Qualifying is what stops the vault's several `README.md` files and the
  thirteen ISLP chapters cross-wiring under the existing shortest-path
  ambiguity rule.
- **Miss** → bare `[[Determinism]]`. Hollow node, creates on click.

`build_link_index` is a full-vault `rglob` plus a frontmatter parse per note.
It is built **once per job and threaded down**, exactly as `VaultIndex`
already is in `pipeline._run`. Per-file would mean twenty vault walks for a
twenty-file ingest.

### Neighbours

`retrieve_result(index, query=body[:1500], k=8, expand_links=False)`, then drop
the note's own source and itself, dedupe by path, keep entries above the
existing `MIN_SIMILARITY` floor, take the top three. `expand_links=False`
because the goal is first-order neighbours, not neighbours-of-neighbours.

**Study guides need no index.** `generate_study_guide` already holds the
`corpus` chunks it cited, so its neighbours are the materials it actually drew
from — better than a fresh similarity query, and one less dependency to
thread.

### Structural links

- **Source.** Today's `[[Human-Freedom-by-John-Kavanaugh]]` strips the
  extension and resolves to nothing. `[[<path>.pdf|<stem>]]` resolves to the
  attachment. This is a live bug fixed inside the feature.
- **Course.** `[[15-Courses/ETHICS/course|ETHICS]]` whenever `course_of()`
  returns a code.

### What lands in the file

```markdown
---
title: Human-Freedom-by-John-Kavanaugh — summary
type: note
generated_by: argus
note_style: summary
source: 15-Courses/ETHICS/materials/Human-Freedom-by-John-Kavanaugh.pdf
course: ETHICS
prompt: ''
topics: [Determinism, Free Will, Self-Possession, Existentialism]
tags: [argus/note, course/ETHICS, topic/determinism, topic/free-will]
related: ["[[Determinism]]", "[[60-Knowledge/.../Existentialism|Existentialism]]"]
---

<body, with the Topics section removed>

<!-- argus:relations:start -->
## Related

**Concepts** — [[Determinism]] · [[Free Will]] · [[Self-Possession]]

**Also in your vault**
- [[15-Courses/ETHICS/notes/Sartre-Lecture.notes|Sartre Lecture]]

**Source** — [[15-Courses/.../Human-Freedom-by-John-Kavanaugh.pdf|Human-Freedom-by-John-Kavanaugh]]
**Course** — [[15-Courses/ETHICS/course|ETHICS]]
<!-- argus:relations:end -->
```

Three decisions in that shape:

1. **The HTML-comment fence** is what makes relinking safe. A relink replaces
   exactly that region and nothing else, so a hand-edit to a generated note's
   body survives. Without a fence, relinking either duplicates the section or
   has to guess where it begins.
2. **Links live in the body, not only in frontmatter.**
   `rag/chunk.py::WIKILINK_RE` scans block text, not frontmatter, so body
   links are what feed retrieval's one-hop expansion. Frontmatter `related:`
   ships too — core Properties reads it, and it matches the Concept Template
   convention — but it is not the load-bearing copy.
3. **Tags nest**, so `tag:#topic` matches every child and `argus/note`
   separates what Argus wrote from what the user wrote in one click.

Accepted tradeoff: the Related region becomes its own chunk of near-pure
links. Its embedding is weak so it rarely wins a similarity contest, and its
`wikilinks` metadata is exactly what expansion needs. If it does pollute
retrieval, the fix is to strip the fenced text from chunking while folding its
links into the first chunk's metadata — more code, so not until measured.

### Study guides

`generate_study_guide` gains the frontmatter it never had (`title`,
`type: guide`, `generated_by`, `course`, `scope`, `topics`, `tags`, `related`)
and the same `Relations` pass, through one shared
`relations.render(front, body, relations)`. A guide and the note beside it are
the same artifact; the codebase already makes this argument for
`note_quality()` and `math_contract()`.

### Relink

A new `kind="relink"` on the existing `ingest_jobs` store — the same seam
reindex and study generation already run on, so it inherits the segmented
progress readout and the one-job-at-a-time slot. It should take that slot: it
contends for the embedding model and the git index like everything else.

| Property | Behaviour |
|---|---|
| Guard | Frontmatter `generated_by == "argus"`. A note without it is skipped, always. |
| Snapshot | One `snapshot_vault` for the whole job — not one per file, which races on `.git/index.lock`. |
| Frontmatter | Merged; never deletes a key it did not add. |
| Body | Replaces the fenced region only. Idempotent: a second run is a no-op. |
| Index | Re-upserts each rewritten note so retrieval sees the new links. |
| Surface | `POST /api/notes/relink`, `RELINK NOTES` on `/sources`, `argus relink [--dry-run]`. |

Writes go through `update_note()` (which snapshots), never `write_text`.

### Privacy — invariant I3

Holds by construction: the index already excludes `99-Private/` and `#no-ai`,
and `build_link_index` excludes the same set, so neither the resolver path nor
the neighbour path can produce a link into the private zone. Pinned by its own
test file rather than trusted.

## Testing

Every regression test is run against the broken code first and must **fail**
before it is accepted. On 2026-08-29 several first drafts passed against the
very bug they were written to catch.

| File | What it pins |
|---|---|
| `tests/vault/test_relations.py` | Normalisation table; a model that ignores the tail; dedupe and caps; resolved vs hollow rendering; cross-folder alias qualification; fence idempotence (render twice → identical bytes; render over an existing fence → replaced, not duplicated); a hand-edited body survives |
| `tests/vault/test_relations_privacy.py` | I3 — a `99-Private/` note and a `#no-ai` note are never linked, from either path |
| `tests/features/ingest/test_notes_relations.py` | `note_markdown` shape end to end; the PDF source link carries `.pdf`; course link present inside a course, absent outside one |
| `tests/features/study/test_guide_relations.py` | The guide gains frontmatter it never had; cited materials become its neighbours |
| `tests/features/notes/test_relink.py` | A user note is untouched; a second run is a no-op; dry-run writes nothing; the index re-upsert happens |
| `web/e2e/relations.spec.ts` | One spec — ingest a fixture, assert the written note contains the Related region. One only: back-to-back ingest cycles lose jobs to the 409 slot and surface as a missing row, not an error |

Gates: `ruff check .`, `pytest -q` (from `.venv`, or collection fails),
`tsc --noEmit`, `next lint`, `next build`, Playwright; then `test / python`,
`test / web` and `test / e2e` on the PR. Two Playwright specs fail on `main`
already and a third is the intermittent server-death problem recorded in
`docs/BUILD_STATE.md` — the delta is what matters, not the raw count.

## Out of scope

- `Taxonomy.knowledge` (see Decisions).
- Auto-created concept stubs.
- Physical backlinks into user notes.
- Practice exams and flashcards — same family and same shared module, but a
  follow-up branch.
