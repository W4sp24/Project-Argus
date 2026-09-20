"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ui/useConfirm";
import GenerateDialog from "@/components/notebook/GenerateDialog";
import {
  createDeck,
  deleteDeck,
  updateDeck,
  useDueSummary,
  useFlashcardDecks,
  type FlashcardDeck,
} from "@/lib/api";

/** How a deck came to exist, shown so a generated deck is never mistaken for one you wrote. */
const SOURCE_LABEL: Record<string, string> = {
  manual: "written",
  imported: "imported",
  generated: "generated",
};

/** "15-Courses/CS201/materials/lecture-04.pdf" -> "lecture-04.pdf". */
function filename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * What a deck was written from, in one line.
 *
 * Named files rather than a count, because "from lecture-04.pdf" answers the
 * question a count only restates. Two is the limit: past that the line runs
 * longer than the deck's own name and stops being scannable.
 */
function provenance(paths: string[]): string {
  if (paths.length === 0) return "";
  const shown = paths.map(filename);
  if (shown.length <= 2) return " · from " + shown.join(", ");
  return " · from " + shown[0] + " +" + (shown.length - 1) + " more";
}

/**
 * The deck library.
 *
 * A deck is the noun here; the study modes are verbs applied to it, which is
 * why this list leads to a deck page rather than straight into a session.
 *
 * Creating one is deliberately trivial and produces an empty deck: filling it
 * is a separate act with four routes (typed, pasted, imported from a note,
 * generated from sources). The old flow fused the two — "create a deck" meant
 * "parse this one file" — and since nothing in Argus ever wrote that file,
 * every attempt failed.
 */
export default function DeckList() {
  const { data: decks, mutate: refresh } = useFlashcardDecks();
  const { data: due } = useDueSummary();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [course, setCourse] = useState("");
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [query, setQuery] = useState("");

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  // A rename commits on Enter *or* on the field losing focus, and Escape has to
  // win when both fire from one keystroke -- it closes the field, which blurs
  // it. Recording the cancel here makes that trailing blur a no-op instead of a
  // second, unwanted save.
  const cancelledRef = useRef(false);

  const dueFor = (deckId: number) =>
    due?.decks.find((entry) => entry.deck_id === deckId)?.due ?? 0;

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim() || creating) return;
    setCreating(true);
    try {
      const deck = await createDeck({ title, course: course.trim() });
      show(`deck :: ${deck.title} created — add some cards`);
      setTitle("");
      setCourse("");
      setShowForm(false);
      await refresh();
    } catch (error) {
      show(`could not create the deck: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setCreating(false);
    }
  }

  function startRename(deck: FlashcardDeck) {
    setEditingId(deck.id);
    setEditValue(deck.title);
  }

  function cancelRename() {
    cancelledRef.current = true;
    setEditingId(null);
  }

  async function commitRename(deck: FlashcardDeck) {
    if (cancelledRef.current) {
      cancelledRef.current = false;
      return;
    }
    setEditingId(null);
    const next = editValue.trim();
    // Blank means "cancel", not "clear the name": the backend refuses an empty
    // title, so sending it would only bounce back as an error.
    if (!next || next === deck.title) return;
    try {
      await updateDeck(deck.id, { title: next });
    } catch (error) {
      show(`rename failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      // Refetch either way: on success to show the new name, on failure so the
      // row snaps back to what the server actually holds.
      await refresh();
    }
  }

  function onEditKeyDown(event: KeyboardEvent<HTMLInputElement>, deck: FlashcardDeck) {
    if (event.key === "Enter") {
      event.preventDefault();
      void commitRename(deck);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  }

  async function remove(deck: FlashcardDeck) {
    const answer = await confirm({
      label: `Delete ${deck.title}`,
      message: `Delete "${deck.title}"?`,
      detail:
        `This removes its ${deck.cards} card${deck.cards === 1 ? "" : "s"} and every review ` +
        "recorded against them. Any flashcards.md you exported stays in the vault.",
      confirmLabel: "DELETE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteDeck(deck.id);
      show(`deck :: ${deck.title} deleted`);
      await refresh();
    } catch (error) {
      show(`delete failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    }
  }

  const filtered = (decks ?? []).filter((deck) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return `${deck.title} ${deck.course} ${deck.description}`.toLowerCase().includes(needle);
  });
  // Exactly one deck gets the solid call to action: the one with the most work
  // waiting. Two primary buttons on a grid is two answers to "where do I start".
  const busiestId = [...(decks ?? [])].sort((a, b) => dueFor(b.id) - dueFor(a.id))[0]?.id;

  return (
    <>
      {confirmDialog}

      {generating && (
        <GenerateDialog
          kind="deck"
          // No `sources` prop, deliberately: there is no SOURCES rail out here,
          // so the dialog has to ask what to read rather than assume the whole
          // course. Passing one would put it back in Course Hub shape.
          onClose={() => {
            setGenerating(false);
            void refresh();
          }}
        />
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label htmlFor="deck-search" className="sr-only">
          Search decks
        </label>
        <input
          id="deck-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search decks"
          className="min-h-10 min-w-0 flex-1 rounded-ctl border border-nb-line bg-nb-panel px-3.5 py-2.5 text-body text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
        />
        {/* Generation lives here as well as in a Course Hub: a hub knows which
            sources you ticked, but needing to walk into one just to make a deck
            is the friction this removes. */}
        <button
          type="button"
          onClick={() => setGenerating(true)}
          className="min-h-10 rounded-ctl border border-nb-line bg-nb-panel px-3.5 py-2.5 text-ctl text-nb-ink transition-colors hover:border-nb-lineHi"
        >
          ✨ Generate
        </button>
        <button
          type="button"
          onClick={() => setShowForm((value) => !value)}
          className="min-h-10 rounded-ctl bg-[var(--ac)] px-4 py-2.5 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90"
        >
          {showForm ? "Cancel" : "＋ New deck"}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={create}
          className="mb-4 flex flex-wrap items-end gap-3 rounded-card border border-nb-line bg-nb-panel p-5"
        >
          <label className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="text-ctl text-nb-body">Deck title</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="e.g. CS201 — graph algorithms"
              className="min-h-10 rounded-ctl border border-nb-line bg-nb-void px-3 py-2.5 text-body text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
            />
          </label>
          <label className="flex w-40 flex-col gap-1.5">
            <span className="text-ctl text-nb-body">Course (optional)</span>
            <input
              value={course}
              onChange={(event) => setCourse(event.target.value)}
              placeholder="CS201"
              className="min-h-10 rounded-ctl border border-nb-line bg-nb-void px-3 py-2.5 font-mono text-label uppercase text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
            />
          </label>
          <button
            type="submit"
            disabled={!title.trim() || creating}
            className="min-h-10 rounded-ctl bg-[var(--ac)] px-4 py-2.5 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-70"
          >
            {creating ? "Creating…" : "Create"}
          </button>
        </form>
      )}

      {!decks ? (
        <p className="text-body text-nb-faint">Loading decks…</p>
      ) : (
        /* Still a list, even as a grid: a deck library is a list of decks, and
           keeping `<ul>/<li>` is what lets a test scope to one card by role
           rather than by a testid bolted on for the purpose. */
        <ul className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((deck) => {
            const dueCount = dueFor(deck.id);
            const complete = deck.cards > 0 && deck.mastered === deck.cards;
            const share = deck.cards > 0 ? (deck.mastered / deck.cards) * 100 : 0;
            return (
              <li
                key={deck.id}
                className={`flex flex-col gap-3 rounded-card border bg-nb-panel p-[1.125rem] transition-colors ${
                  deck.id === busiestId && dueCount > 0
                    ? "border-[var(--ac)]"
                    : "border-nb-line hover:border-nb-lineHi"
                }`}
              >
                <div>
                  <div className="flex items-baseline gap-2">
                    <span
                      className={`truncate font-mono text-meta font-semibold tracking-[0.06em] ${
                        deck.course ? "text-[var(--ac)]" : "text-nb-faint"
                      }`}
                    >
                      {deck.course || "No course"}
                    </span>
                    {dueCount > 0 && (
                      <span className="ml-auto shrink-0 rounded-full bg-nb-acBg px-2.5 py-0.5 text-meta font-semibold text-[var(--ac)]">
                        {dueCount} due
                      </span>
                    )}
                    {/* Constant, like ThreadRail's, not `Rename ${deck.title}`:
                        a name built from the deck's own title collides with any
                        other control whose name it happens to contain. The card
                        is found by its text; this is reached through the card. */}
                    <button
                      type="button"
                      aria-label="Rename deck"
                      onClick={() => startRename(deck)}
                      className={`shrink-0 text-nb-faint transition-colors hover:text-nb-ink ${
                        dueCount > 0 ? "" : "ml-auto"
                      }`}
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${deck.title}`}
                      onClick={() => void remove(deck)}
                      className="shrink-0 text-nb-faint transition-colors hover:text-danger"
                    >
                      ×
                    </button>
                  </div>

                  {editingId === deck.id ? (
                    <input
                      autoFocus
                      value={editValue}
                      onChange={(event) => setEditValue(event.target.value)}
                      onKeyDown={(event) => onEditKeyDown(event, deck)}
                      onFocus={() => {
                        cancelledRef.current = false;
                      }}
                      onBlur={() => void commitRename(deck)}
                      // "Deck name", not "Deck title": the create form above owns
                      // that label, and one page-level query matching two fields
                      // is a strict-mode failure in every test that types here.
                      aria-label="Deck name"
                      className="mt-1.5 min-h-9 w-full rounded-ctl border border-nb-lineHi bg-nb-void px-2.5 py-1.5 text-lead font-semibold text-nb-ink"
                    />
                  ) : (
                    <Link
                      href={`/notebook/flashcards/${deck.id}`}
                      onDoubleClick={(event) => {
                        event.preventDefault();
                        startRename(deck);
                      }}
                      className="mt-1.5 block truncate text-lead font-semibold text-nb-ink"
                    >
                      {deck.title}
                    </Link>
                  )}

                  <p className="mt-1 truncate text-label text-nb-faint">
                    {deck.cards} card{deck.cards === 1 ? "" : "s"} ·{" "}
                    {SOURCE_LABEL[deck.source] ?? deck.source}
                    {/* What a generated deck was asked for, and what it read. A
                        job row is transient; this is where you look weeks later
                        wondering why one deck is harder than another, or which
                        lecture it came out of. */}
                    {deck.description ? ` · ${deck.description}` : ""}
                    {provenance(deck.source_paths)}
                  </p>
                </div>

                <div>
                  <div className="h-1.5 overflow-hidden rounded-bar bg-nb-track">
                    <span
                      className={`block h-1.5 ${complete ? "bg-ok" : "bg-[var(--ac)]"}`}
                      style={{ width: `${share}%` }}
                    />
                  </div>
                  <p className={`mt-1.5 text-label ${complete ? "text-ok" : "text-nb-body"}`}>
                    {deck.cards === 0
                      ? "No cards yet"
                      : complete
                        ? `All ${deck.cards} mastered${dueCount === 0 ? " — nothing due" : ""}`
                        : `${deck.mastered} of ${deck.cards} mastered`}
                  </p>
                </div>

                <Link
                  href={`/notebook/flashcards/${deck.id}/${dueCount > 0 ? "review" : "cards"}`}
                  className={`rounded-ctl py-2.5 text-center text-ctl font-semibold transition-opacity hover:opacity-90 ${
                    deck.id === busiestId && dueCount > 0
                      ? "bg-[var(--ac)] text-nb-onAc"
                      : "border border-nb-line text-nb-ink"
                  }`}
                >
                  {dueCount > 0 ? `Review ${dueCount}` : "Browse"}
                </Link>
              </li>
            );
          })}

          {/* Always last, and always present: the empty state and the "one more"
              affordance are the same thing, so there is no separate zero case
              saying something different. Tests scope a deck by its text, so
              this extra listitem never matches one of them.

              "Start from scratch", not the artboard's "New deck": that would
              give this and the toolbar button the same accessible name, and
              `getByRole` matches names by substring — two controls answering to
              one name is a strict-mode failure waiting to happen and, worse, an
              ambiguous announcement for anyone using a screen reader. It also
              reads better opposite "✨ Generate". */}
          <li className="flex">
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="flex min-h-[11.25rem] flex-1 flex-col justify-center gap-1.5 rounded-card border border-dashed border-nb-line p-[1.125rem] text-left transition-colors hover:border-nb-lineHi"
            >
              <span className="text-body font-semibold text-nb-ink">Start from scratch</span>
              {/* No "generate from a lecture" here, tempting as the artboard's
                  wording is: the toolbar's ✨ Generate sits inches away, and a
                  description containing that word makes both buttons answer to
                  it — `getByRole` matches names by substring. */}
              <span className="text-label text-nb-faint">
                Type cards, paste rows, or pull <span className="font-mono">Q::</span>/
                <span className="font-mono">A::</span> pairs out of a note.
              </span>
            </button>
          </li>
        </ul>
      )}

      {decks && decks.length > 0 && filtered.length === 0 && (
        <p className="mt-4 text-body text-nb-faint">
          No deck matches “{query.trim()}”.
        </p>
      )}
    </>
  );
}
