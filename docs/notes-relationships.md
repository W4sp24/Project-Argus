# Note relationships

Every note Argus writes now says what it is about and what that connects to.
A note is no longer a leaf in your vault: Obsidian's graph, backlinks panel
and tag search can all reach it, and so can Argus's own retrieval.

Nothing to turn on. Ingest a file, or generate a study guide, and the
relationships come with it.

---

## 1. What lands in a note

At the bottom of every generated note and study guide there is a **Related**
section:

```markdown
<!-- argus:relations:start -->
## Related

**Concepts** — [[Determinism]] · [[Free Will]] · [[Self-Possession]]

**Also in your vault**
- [[15-Courses/ETHICS/notes/Sartre-Lecture.notes|Sartre Lecture]]

**Source** — [[15-Courses/ETHICS/materials/Human-Freedom.pdf|Human-Freedom]]
**Course** — [[15-Courses/ETHICS/course|ETHICS]]
<!-- argus:relations:end -->
```

Four kinds of link, and they answer different questions:

| | What it is | Where it comes from |
|---|---|---|
| **Concepts** | What the document is *about* | The model names them; Argus checks each one against your actual vault |
| **Also in your vault** | What else you already have on this | Nearest notes in the search index, deduplicated by file |
| **Source** | The file this note was made from | The path Argus actually saved |
| **Course** | The course hub, when the file was a course material | The folder the file went into |

The frontmatter carries the same relationships in a form Obsidian's Properties
panel and any query plugin can read:

```yaml
topics: [Determinism, Free Will, Self-Possession]
tags: [argus/note, course/ETHICS, topic/determinism, topic/free-will]
related: ["[[Determinism]]", "[[60-Knowledge/General/Existentialism|Existentialism]]"]
```

The links in the body are the ones that matter most, and that is deliberate:
Obsidian builds its graph and its backlinks from body links, and so does
Argus's own one-hop retrieval expansion. The frontmatter copy is for reading
and querying.

---

## 2. Solid links and hollow ones

Some concept links point at a note you have written. Others do not — and those
are written anyway.

- **Solid** — the concept already exists in your vault. The link is
  path-qualified with a readable alias, so `[[60-Knowledge/Artificial
  Intelligence/Linear Regression|Linear Regression]]` reads as the concept but
  can only ever open the one right file. This matters in a vault with five
  `README.md` files and thirteen ISLP chapters.
- **Hollow** — you have not written that note yet. Obsidian shows the link in
  a dimmer colour and puts a hollow node in the graph.

A hollow link is a feature, not a gap. **Click it and Obsidian creates the
note** — and because `85-Templates/Concept Template.md` is wired as your
templates folder, the new note starts with `type: concept`, `domain:` and
`related:` already in place.

Read the graph this way: solid edges are what you have written up, hollow ones
are what you have covered but not yet processed. That second set is the useful
one.

Argus never invents more than seven concepts per note, and normalises them
first — `determinism`, `Determinism` and `**Determinism**` all become one node
rather than three.

---

## 3. Querying the vault

No plugin required; this is all core Obsidian.

| You want | Search for |
|---|---|
| Everything Argus wrote | `tag:#argus` |
| Just the notes, or just the guides | `tag:#argus/note` · `tag:#argus/guide` |
| One course's material | `tag:#course/ETHICS` |
| Browse by concept | `tag:#topic` |
| One concept | `tag:#topic/free-will` |

And the two that need no search at all:

- **Backlinks panel.** Open any concept note and every lecture that touched it
  is listed. Argus wrote nothing into that note to make this happen — Obsidian
  derives backlinks from the forward links, which is exactly why Argus does
  not edit your notes to create them.
- **Local graph.** Open a note, open the local graph, and you see the
  concepts, the neighbours, the source and the course around it.

---

## 4. Catching up notes you already have

Notes written before this feature existed have no Related section. Two ways to
backfill them.

**From the app:** `/sources` → **Relink notes**. It reports how many notes it
found and runs through the same progress readout an ingest uses.

**From the command line:**

```bash
argus relink --dry-run   # says what would change, writes nothing
argus relink             # does it
```

Either way:

- **Only notes carrying `generated_by: argus` are touched.** A note you wrote
  by hand is never rewritten, even if it sits in the same folder.
- **Your edits survive.** Argus replaces only what is between the
  `argus:relations` comment markers. A paragraph you added to a generated note
  stays exactly where you put it.
- **Frontmatter is merged, never replaced.** A key you added by hand is kept.
- **It is safe to run twice.** The second run changes nothing and says so.
- The vault is git-snapshotted once before the first note, so the whole
  backfill is one undo point.

A relink loads the embedding model and re-indexes each note it changes, so it
takes the same slot as an ingest or a reindex — start one while another is
running and Argus will tell you to wait rather than running both.

---

## 5. What Argus will not do

- **It will not write into your notes to create backlinks.** Obsidian derives
  those from forward links already, so writing them would be duplicated state
  in files Argus does not own.
- **It will not create empty concept notes.** A hollow link is a prompt to
  write one; a vault full of empty stubs is not.
- **It will not link to `99-Private/` or to anything tagged `#no-ai`** — not as
  a concept, not as a neighbour, not even when a private note claims a public
  concept as an alias. The link is written hollow instead.

---

## 6. If a note has no concepts

The concepts come from the model, and a small or busy model sometimes skips
them. When that happens the note is written exactly as it would have been
before this feature existed — body, source link, course link — just without
the concept row. Nothing fails and nothing is lost.

A relink will not recover them: it recomputes links from the topics a note
already records, and cannot invent ones the model never named. To get concepts
onto such a note, re-ingest the source, or add them to the note's `topics:`
frontmatter yourself and relink — a relink honours what it finds there.

If it happens often, the model is the thing to change. A larger local model,
or a hosted one, follows the instruction reliably.
