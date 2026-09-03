"use client";

import { useState } from "react";
import Link from "next/link";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import GenerateDialog from "@/components/notebook/GenerateDialog";
import {
  createDeck,
  deleteDeck,
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

  return (
    <Panel
      label="DECKS"
      headerRight={
        <span className="flex gap-2">
          {/* Generation lives here as well as in a Course Hub: a hub knows
              which sources you ticked, but needing to walk into one just to
              make a deck is the friction this removes. */}
          <Button variant="quiet" onClick={() => setGenerating(true)}>
            ✨ GENERATE
          </Button>
          <Button variant="quiet" onClick={() => setShowForm((value) => !value)}>
            {showForm ? "CANCEL" : "+ NEW DECK"}
          </Button>
        </span>
      }
    >
      {confirmDialog}

      {generating && (
        <GenerateDialog
          kind="deck"
          // No SOURCES rail out here, so the whole course is the corpus.
          sources={null}
          onClose={() => {
            setGenerating(false);
            void refresh();
          }}
        />
      )}

      {showForm && (
        <form onSubmit={create} className="mb-4 flex flex-wrap items-end gap-2 border-b border-line pb-4">
          <label className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Deck title
            </span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="e.g. CS201 — graph algorithms"
              className="min-h-9 border border-line bg-sunken px-2 py-1.5 font-body text-body text-ink focus:border-lineHi"
            />
          </label>
          <label className="flex w-40 flex-col gap-1">
            <span className="font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Course (optional)
            </span>
            <input
              value={course}
              onChange={(event) => setCourse(event.target.value)}
              placeholder="CS201"
              className="min-h-9 border border-line bg-sunken px-2 py-1.5 font-mono text-label uppercase text-ink focus:border-lineHi"
            />
          </label>
          <Button type="submit" disabled={!title.trim() || creating}>
            {creating ? "CREATING…" : "CREATE"}
          </Button>
        </form>
      )}

      {!decks ? (
        <p className="text-body text-ink-faint">Loading decks…</p>
      ) : decks.length === 0 ? (
        <p className="text-body text-ink-faint">
          No decks yet. Create one above, then fill it by typing cards, pasting rows, importing a
          note&apos;s <code className="font-mono text-label">Q::</code>/
          <code className="font-mono text-label">A::</code> pairs, or generating from a
          course&apos;s sources.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {decks.map((deck) => {
            const dueCount = dueFor(deck.id);
            return (
              <li
                key={deck.id}
                className="flex items-center gap-2 border border-line px-3 py-2 transition-colors hover:border-lineHi"
              >
                <Link href={`/notebook/flashcards/${deck.id}`} className="min-w-0 flex-1">
                  <span className="block truncate text-body text-ink">{deck.title}</span>
                  <span className="block truncate font-mono text-meta text-ink-faint">
                    {deck.cards} card{deck.cards === 1 ? "" : "s"}
                    {deck.course ? ` · ${deck.course}` : ""} ·{" "}
                    {SOURCE_LABEL[deck.source] ?? deck.source}
                    {/* What a generated deck was asked for. A job row is
                        transient; this is where you look weeks later
                        wondering why one deck is harder than another. */}
                    {deck.description ? ` · ${deck.description}` : ""}
                  </span>
                </Link>
                {dueCount > 0 && (
                  <span className="shrink-0 border border-[var(--ac)] bg-[var(--ac-bg)] px-1.5 py-0.5 font-mono text-meta text-[var(--ac)]">
                    {dueCount} due
                  </span>
                )}
                <Button
                  variant="quiet"
                  aria-label={`Delete ${deck.title}`}
                  onClick={() => void remove(deck)}
                >
                  ×
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
