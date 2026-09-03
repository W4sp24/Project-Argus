"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Markdown from "@/components/Markdown";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
import Button from "@/components/ui/Button";
import { postMatchScore, useMatchBest, type FlashcardDeckDetail } from "@/lib/api";
import { shuffle } from "@/lib/flashcards/distractors";

/** Pairs per board. Twelve tiles is the most that stays scannable at a glance. */
const PAIRS = 6;

/** Fewer than this and there is no game to play. */
const MIN_PAIRS = 2;

interface Tile {
  id: string;
  ref: string;
  text: string;
  side: "front" | "back";
}

function formatMs(ms: number): string {
  const seconds = ms / 1000;
  return `${seconds.toFixed(1)}s`;
}

/**
 * Pair terms against the clock.
 *
 * **Click-to-pair, not drag.** Drag is hostile to Playwright, worse on touch,
 * and buys nothing here — the interaction is "these two go together", and two
 * clicks say that as well as a drag does.
 *
 * A game, so it touches no schedule: its scores live in
 * `flashcard_match_scores`, nowhere near `flashcard_reviews`. That is the same
 * line BROWSE draws, and for the same reason — the fun mode must not be able
 * to corrupt weeks of spacing.
 */
export default function MatchGame({ deck }: { deck: FlashcardDeckDetail }) {
  const { data: best, mutate: refreshBest } = useMatchBest(deck.id);

  const [seed, setSeed] = useState(0);
  const [selected, setSelected] = useState<Tile | null>(null);
  const [cleared, setCleared] = useState<string[]>([]);
  const [wrong, setWrong] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [finishedMs, setFinishedMs] = useState<number | null>(null);

  const cards = useMemo(() => {
    void seed; // a new seed deals a new board
    return shuffle(deck.card_list.filter((card) => !card.suspended)).slice(0, PAIRS);
  }, [deck.card_list, seed]);

  const tiles = useMemo(
    () =>
      shuffle(
        cards.flatMap((card) => [
          { id: `${card.ref}:front`, ref: card.ref, text: card.front, side: "front" as const },
          { id: `${card.ref}:back`, ref: card.ref, text: card.back, side: "back" as const },
        ]),
      ),
    [cards],
  );

  // One interval, and only while a round is actually running.
  useEffect(() => {
    if (startedAt === null || finishedMs !== null) return;
    const id = window.setInterval(() => setElapsed(Date.now() - startedAt), 100);
    return () => window.clearInterval(id);
  }, [startedAt, finishedMs]);

  const finish = useCallback(
    async (ms: number) => {
      setFinishedMs(ms);
      try {
        await postMatchScore(deck.id, Math.round(ms), cards.length);
        await refreshBest();
      } catch {
        // A lost score is not worth an error dialog over a game.
      }
    },
    [cards.length, deck.id, refreshBest],
  );

  function choose(tile: Tile) {
    if (cleared.includes(tile.ref) || finishedMs !== null) return;
    if (startedAt === null) setStartedAt(Date.now());

    if (selected === null) {
      setSelected(tile);
      return;
    }
    if (selected.id === tile.id) {
      setSelected(null);
      return;
    }
    // Two halves of the same card, and not the same half twice.
    if (selected.ref === tile.ref && selected.side !== tile.side) {
      const next = [...cleared, tile.ref];
      setCleared(next);
      setSelected(null);
      if (next.length === cards.length) {
        void finish(Date.now() - (startedAt ?? Date.now()));
      }
      return;
    }
    setWrong(tile.id);
    window.setTimeout(() => setWrong(null), 250);
    setSelected(null);
  }

  function replay() {
    setSeed((value) => value + 1);
    setSelected(null);
    setCleared([]);
    setWrong(null);
    setStartedAt(null);
    setElapsed(0);
    setFinishedMs(null);
  }

  if (cards.length < MIN_PAIRS) {
    return (
      <ActivityChrome deckId={deck.id} deckTitle={deck.title} activity="match">
        <p className="text-body text-ink-faint">
          Match needs at least {MIN_PAIRS} cards to be a game. This deck has {cards.length}.
        </p>
      </ActivityChrome>
    );
  }

  return (
    <ActivityChrome
      deckId={deck.id}
      deckTitle={deck.title}
      activity="match"
      progress={formatMs(finishedMs ?? elapsed)}
      keys="click a term, then its definition · this changes nothing about your schedule"
    >
      {finishedMs !== null ? (
        <div className="border border-line p-5">
          <p className="font-mono text-label uppercase tracking-[0.16em] text-[var(--ac)]">
            {best?.best_ms !== null && best?.best_ms !== undefined && finishedMs <= best.best_ms
              ? "new best"
              : "finished"}
          </p>
          <p className="mt-2 text-lead text-ink-bright">
            {cards.length} pairs in {formatMs(finishedMs)}
          </p>
          {best?.best_ms !== null && best?.best_ms !== undefined && (
            <p className="mt-1 font-mono text-label text-ink-muted">
              best :: {formatMs(best.best_ms)}
            </p>
          )}
          <Button className="mt-4" onClick={replay}>
            PLAY AGAIN
          </Button>
        </div>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-3 font-mono text-meta text-ink-faint">
            <span>
              {cleared.length} / {cards.length} paired
            </span>
            {best?.best_ms !== null && best?.best_ms !== undefined && (
              <span>best :: {formatMs(best.best_ms)}</span>
            )}
          </div>

          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {tiles.map((tile) => {
              const done = cleared.includes(tile.ref);
              const isSelected = selected?.id === tile.id;
              return (
                <li key={tile.id}>
                  <button
                    type="button"
                    disabled={done}
                    onClick={() => choose(tile)}
                    className={`flex h-24 w-full items-center justify-center overflow-auto border p-2 text-center text-label transition-colors ${
                      done
                        ? "border-line/40 text-ink-faint/30"
                        : wrong === tile.id
                          ? "border-danger text-danger"
                          : isSelected
                            ? "border-[var(--ac)] bg-[var(--ac-bg)] text-ink-bright"
                            : "border-line text-ink hover:border-lineHi"
                    }`}
                  >
                    {/* `invisible` rather than unmounted: a cleared tile keeps
                        its space, so the board does not reflow under the
                        pointer mid-round. */}
                    <span className={done ? "invisible" : ""}>
                      <Markdown text={tile.text} className="text-label" />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </ActivityChrome>
  );
}
