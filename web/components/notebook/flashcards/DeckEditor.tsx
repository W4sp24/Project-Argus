"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
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
    "min-h-9 w-full border border-line bg-sunken px-2 py-1.5 font-body text-body text-ink focus:border-lineHi";

  return (
    <Panel
      label="CARDS"
      headerRight={
        <span className="font-mono text-meta text-ink-faint">
          {deck.cards} card{deck.cards === 1 ? "" : "s"}
        </span>
      }
    >
      {confirmDialog}

      <form onSubmit={add} className="mb-4 border-b border-line pb-4">
        <div className="grid gap-2 md:grid-cols-[1fr_1fr_160px_auto]">
          <label className="min-w-0">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Front
            </span>
            <input
              value={front}
              onChange={(event) => setFront(event.target.value)}
              placeholder="the question"
              className={cell}
            />
          </label>
          <label className="min-w-0">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Back
            </span>
            <input
              value={back}
              onChange={(event) => setBack(event.target.value)}
              placeholder="the answer"
              className={cell}
            />
          </label>
          <label className="min-w-0">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Hint
            </span>
            <input
              value={hint}
              onChange={(event) => setHint(event.target.value)}
              placeholder="optional"
              className={cell}
            />
          </label>
          <div className="flex items-end">
            <Button type="submit" disabled={!front.trim() || !back.trim() || adding}>
              + ADD CARD
            </Button>
          </div>
        </div>
        <p className="mt-2 font-mono text-micro text-ink-faint">
          Markdown and $LaTeX$ both render on the card.
        </p>
      </form>

      {deck.card_list.length === 0 ? (
        <p className="text-body text-ink-faint">
          No cards yet — add one above, or use IMPORT to paste rows or pull{" "}
          <code className="font-mono text-label">Q::</code>/
          <code className="font-mono text-label">A::</code> pairs out of a note.
        </p>
      ) : (
        <ul className="space-y-2">
          {deck.card_list.map((card, index) => (
            <li key={card.ref} className="grid gap-2 md:grid-cols-[1fr_1fr_160px_auto]">
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
              <div className="flex items-center gap-1">
                <Button
                  variant="quiet"
                  aria-label={card.starred ? `Unstar card ${index + 1}` : `Star card ${index + 1}`}
                  aria-pressed={card.starred}
                  onClick={() => void toggleStar(card)}
                  className={card.starred ? "text-[var(--ac)]" : ""}
                >
                  {card.starred ? "★" : "☆"}
                </Button>
                <Button
                  variant="quiet"
                  aria-label={`Move card ${index + 1} up`}
                  disabled={index === 0}
                  onClick={() => void move(index, -1)}
                >
                  ▲
                </Button>
                <Button
                  variant="quiet"
                  aria-label={`Move card ${index + 1} down`}
                  disabled={index === deck.card_list.length - 1}
                  onClick={() => void move(index, 1)}
                >
                  ▼
                </Button>
                <Button
                  variant="quiet"
                  aria-label={`Delete card ${index + 1}`}
                  onClick={() => void remove(card)}
                >
                  ×
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
