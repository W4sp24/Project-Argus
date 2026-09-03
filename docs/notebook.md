# The Notebook

Study became **Notebook**. Same cyan accent, same three pages, a new name and
two things it could not do before: run in a window of its own, and hold
flashcards you actually wrote.

Old `/study` links still work — `/study` and `/study/*` redirect permanently to
`/notebook`, so bookmarks, `obsidian://` deep links and anything in your vault
that references the old path keep resolving.

## A window of its own

`POP OUT ↗` in the Notebook header moves the whole mode into a second window:
the overview, the Course Hub, flashcards and every study activity, and the
practice exam.

- In the **desktop app** it is a real second OS window. It remembers its size
  and position, and closing the main window closes it.
- In a **browser** it is a second browser window. If nothing opens, your
  browser blocked the pop-up for this origin.

The popped-out window has no mode strip — it exists to hold the Notebook, and a
control that navigated out of it would leave you in a window with no way back.
While it is open, the main window's NOTEBOOK tab reads `NOTEBOOK ↗` and raises
that window instead of showing a second copy.

Both windows talk to one backend, so a generation started in either is visible
in both. The tray in the top bar (`⣿ 2`) lists whatever is running; that work
survives navigating away, reloading, and closing the window it started in.

## Flashcards

A **deck** is the thing you own. The four study modes are things you do to it.

### Getting cards in

Four routes, and only one of them involves a model.

| Route | Where | What it does |
|---|---|---|
| **Type them** | the deck page | Front, back, optional hint. `Tab` between fields; a click away saves. |
| **Paste rows** | IMPORT → PASTE ROWS | Choose what separates front from back (tab, comma, dash) and what separates cards (new line, semicolon). The preview counts what will actually be created. |
| **From a note** | IMPORT → FROM A NOTE | Reads every `Q::` / `A::` pair in any vault note. |
| **Generate** | Course Hub → STUDIO → flashcard deck | Writes cards from the sources you have ticked, in the background. |

The third one is worth knowing about: **every note Argus generates already
carries a `Q::`/`A::` self-test section**, so a lecture note usually imports
as-is. You do not need a special file.

Paste import splits on the *first* delimiter only, so a definition keeps its
own commas: `photosynthesis,light, water, and CO2` is one card, not three.

### Getting cards out

`EXPORT` writes the deck to its course's `flashcards.md` as `Q::`/`A::` pairs —
the same format `FROM A NOTE` reads, so the two round-trip. A deck with no
course has nowhere to land and will say so; give it a course first.

### The four ways to study

| Mode | What it is | Changes your schedule? |
|---|---|---|
| **Review** | Spaced repetition (FSRS). Each grade button shows the real interval it will schedule. `Space` flips, `1`–`4` grade, `U` undoes. | **Yes** — this is the schedule |
| **Flashcards** | Flip through the deck. `←`/`→`, shuffle, star. Turn on *Track progress* to sort into ✗ / ✓ piles and re-run just the ✗ pile. | **No** |
| **Learn** | Rounds of 7. A card you have not answered is multiple choice; once you have it right it asks you to type it. A typo is accepted as a near miss. | **Yes** — same schedule as Review |
| **Match** | Pair terms against the clock, click-to-pair. Keeps your best time per deck. | **No** |

That column is the point of having four modes. **Cramming and scheduling are
different acts.** Flipping through a deck the night before a lecture must not
rewrite spacing you have built over weeks, so Flashcards and Match record
nothing against your cards — the ✗/✓ piles live only for that session. Review
and Learn both feed FSRS, so practising in either one counts.

In Learn, "I was right" counts the card as a *near miss*, never as a clean
recall. A card you had to argue for is not a card you knew.

### Stars, hints and suspending

- **Star** a card to filter to it later. It is a property of the card, so it
  survives the session and shows in the editor.
- A **hint** shows as `GET A HINT` on the card. Using one in Learn caps that
  answer at a near miss.
- **Suspend** a card and it stops appearing in the review queue without being
  deleted — for the card you have decided is not worth the reviews yet.

### Deleting

Deleting a deck removes its cards and every review recorded against them. Any
`flashcards.md` you exported stays in the vault; deleting a deck never touches
your notes.
