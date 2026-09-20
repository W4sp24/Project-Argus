"use client";

import { useState } from "react";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ui/useConfirm";
import { nextReview, type NextReviewTone } from "@/lib/flashcards/schedule";
import {
  addCards,
  deleteCard,
  reorderCards,
  updateCard,
  type FlashcardCard,
  type FlashcardDeckDetail,
} from "@/lib/api";

/**
 * The card list, which is also the editor.
 *
 * Taken straight from how every flashcard tool worth using behaves: the list
 * you browse and the form you edit are one surface, so there is no "manage
 * cards" screen to go and find. Each row is three fields; `Tab` walks them and
 * a blur commits.
 *
 * Reordering is `▲`/`▼` rather than drag-and-drop, deliberately. Drag is
 * hostile to Playwright and worse on touch, and buys nothing for a list you
 * nudge a card up or down in. The server refuses a partial order, so the whole
 * order goes with every move.
 */
export default function DeckEditor({
  deck,
  onChanged,
}: {
  deck: FlashcardDeckDetail;
  onChanged: () => void;
}) {
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [hint, setHint] = useState("");
  const [adding, setAdding] = useState(false);
  // Collapsed by default: the list is what you came for, and a permanently
  // open three-field form pushed the first card below the fold on every deck.
  const [addOpen, setAddOpen] = useState(false);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!front.trim() || !back.trim() || adding) return;
    setAdding(true);
    try {
      await addCards(deck.id, [{ front, back, hint: hint.trim() || null }]);
      setFront("");
      setBack("");
      setHint("");
      onChanged();
    } catch (error) {
      show(`could not add the card: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setAdding(false);
    }
  }

  /** Commit an edit only when the value actually changed — a blur is not a change. */
  async function commit(card: FlashcardCard, field: "front" | "back" | "hint", value: string) {
    const current = card[field] ?? "";
    if (value === current) return;
    try {
      await updateCard(deck.id, card.ref, { [field]: value });
      onChanged();
    } catch (error) {
      show(`could not save: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
      // Re-fetch so the input snaps back to what the server actually holds
      // rather than showing an edit that was refused.
      onChanged();
    }
  }

  async function toggleStar(card: FlashcardCard) {
    await updateCard(deck.id, card.ref, { starred: !card.starred });
    onChanged();
  }

  async function move(index: number, delta: number) {
    const next = [...deck.card_list];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    await reorderCards(
      deck.id,
      next.map((card) => card.ref),
    );
    onChanged();
  }

  async function remove(card: FlashcardCard) {
    const answer = await confirm({
      label: "Delete card",
      message: `Delete "${card.front.slice(0, 60)}"?`,
      detail: "Its review history goes with it.",
      confirmLabel: "DELETE",
      tone: "danger",
    });
    if (answer === null) return;
    await deleteCard(deck.id, card.ref);
    show("card deleted");
    onChanged();
  }

  const cell =
    "min-h-9 w-full rounded-ctl border border-nb-line bg-nb-void px-2.5 py-1.5 font-body text-body text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi";
  const iconButton =
    "shrink-0 px-1 text-nb-faint transition-colors hover:text-nb-ink disabled:opacity-40";
  /** Star / front / back / hint / next review / row actions. */
  const ROW = "grid gap-3 md:grid-cols-[1.25rem_1fr_1fr_9rem_8rem_auto]";
  const TONE: Record<NextReviewTone, string> = {
    due: "text-[var(--ac)]",
    scheduled: "text-ok",
    muted: "text-nb-faint",
  };

  return (
    <NotebookPanel
      heading="Cards"
      headerRight={
        <>
          <span className="text-ctl text-nb-faint">
            {deck.cards} · tab between fields, a click away saves
          </span>
          <button
            type="button"
            form="add-card"
            onClick={() => setAddOpen((open) => !open)}
            aria-expanded={addOpen}
            className="rounded-ctl bg-[var(--ac)] px-3.5 py-2 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90"
          >
            {addOpen ? "Close" : "＋ Add card"}
          </button>
        </>
      }
    >
      {confirmDialog}

      {addOpen && (
        <form
          id="add-card"
          onSubmit={add}
          className="mb-4 rounded-tile border border-nb-line bg-nb-raised p-4"
        >
          <div className="grid gap-3 md:grid-cols-[1fr_1fr_9rem_auto]">
            <label className="min-w-0">
              <span className="mb-1.5 block text-label text-nb-body">Front</span>
              <input
                value={front}
                onChange={(event) => setFront(event.target.value)}
                placeholder="the question"
                className={cell}
              />
            </label>
            <label className="min-w-0">
              <span className="mb-1.5 block text-label text-nb-body">Back</span>
              <input
                value={back}
                onChange={(event) => setBack(event.target.value)}
                placeholder="the answer"
                className={cell}
              />
            </label>
            <label className="min-w-0">
              <span className="mb-1.5 block text-label text-nb-body">Hint</span>
              <input
                value={hint}
                onChange={(event) => setHint(event.target.value)}
                placeholder="optional"
                className={cell}
              />
            </label>
            <div className="flex items-end">
              {/* "Add to deck", not "Add card": the control that opened this
                  form is called "＋ Add card", and `getByRole` matches
                  accessible names by substring — two buttons answering to one
                  name is a strict-mode failure and an ambiguous announcement.
                  "Save" is taken too, by the rename form above. */}
              <button
                type="submit"
                disabled={!front.trim() || !back.trim() || adding}
                className="min-h-9 rounded-ctl bg-[var(--ac)] px-3.5 py-2 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-70"
              >
                Add to deck
              </button>
            </div>
          </div>
        </form>
      )}

      {deck.card_list.length === 0 ? (
        <p className="text-body text-nb-faint">
          No cards yet — add one above, or use Import to paste rows or pull{" "}
          <code className="font-mono text-label">Q::</code>/
          <code className="font-mono text-label">A::</code> pairs out of a note.
        </p>
      ) : (
        <>
          {/* `px-3` matches the rows' own padding, so a heading sits over its
              column rather than a few pixels left of it. */}
          <div className={`${ROW} hidden px-3 pb-2 md:grid`} aria-hidden="true">
            <span />
            <span className="text-label text-nb-faint">Front</span>
            <span className="text-label text-nb-faint">Back</span>
            <span className="text-label text-nb-faint">Hint</span>
            <span className="text-label text-nb-faint">Next review</span>
            <span />
          </div>
          <ul className="flex flex-col gap-2">
            {deck.card_list.map((card, index) => {
              const schedule = nextReview(card.due_at, card.suspended);
              return (
                <li
                  key={card.ref}
                  className={`${ROW} items-center rounded-tile border border-nb-line p-3 ${
                    card.suspended ? "opacity-60" : "bg-nb-raised"
                  }`}
                >
                  <button
                    type="button"
                    aria-label={card.starred ? `Unstar card ${index + 1}` : `Star card ${index + 1}`}
                    aria-pressed={card.starred}
                    onClick={() => void toggleStar(card)}
                    className={`shrink-0 ${card.starred ? "text-warn" : "text-nb-faint"}`}
                  >
                    {card.starred ? "★" : "☆"}
                  </button>
                  <input
                    aria-label={`Front of card ${index + 1}`}
                    defaultValue={card.front}
                    onBlur={(event) => void commit(card, "front", event.target.value)}
                    className={cell}
                  />
                  <input
                    aria-label={`Back of card ${index + 1}`}
                    defaultValue={card.back}
                    onBlur={(event) => void commit(card, "back", event.target.value)}
                    className={cell}
                  />
                  <input
                    aria-label={`Hint for card ${index + 1}`}
                    defaultValue={card.hint ?? ""}
                    placeholder="—"
                    onBlur={(event) => void commit(card, "hint", event.target.value)}
                    className={cell}
                  />
                  {/* Read-only: the schedule is FSRS's to set. Showing it here is
                      the whole reason `CardInfo` grew a `due_at` -- `/due` only
                      ever answered for cards already due. */}
                  <span className={`text-ctl ${TONE[schedule.tone]}`}>{schedule.label}</span>
                  <div className="flex items-center">
                    <button
                      type="button"
                      aria-label={`Move card ${index + 1} up`}
                      disabled={index === 0}
                      onClick={() => void move(index, -1)}
                      className={iconButton}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      aria-label={`Move card ${index + 1} down`}
                      disabled={index === deck.card_list.length - 1}
                      onClick={() => void move(index, 1)}
                      className={iconButton}
                    >
                      ▼
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete card ${index + 1}`}
                      onClick={() => void remove(card)}
                      className={`${iconButton} hover:text-danger`}
                    >
                      ×
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="mt-3.5 text-label text-nb-faint">
            Markdown and $LaTeX$ both render on the card.
          </p>
        </>
      )}
    </NotebookPanel>
  );
}
