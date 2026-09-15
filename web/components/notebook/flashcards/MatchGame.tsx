"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "@/components/Markdown";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
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
  // Held in a ref and cleared on unmount, like the elapsed ticker below: a bare
  // setTimeout kept a reference to a gone component's setState for a quarter of
  // a second after leaving the game, and a fast player queued several.
  const wrongTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (wrongTimer.current) window.clearTimeout(wrongTimer.current);
    },
    [],
  );

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
    if (wrongTimer.current) window.clearTimeout(wrongTimer.current);
    wrongTimer.current = window.setTimeout(() => setWrong(null), 250);
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
        <p className="text-body text-nb-faint">
          Match needs at least {MIN_PAIRS} cards to be a game. This deck has {cards.length}.
        </p>
      </ActivityChrome>
    );
  }

  const bestMs = best?.best_ms ?? null;
  const running = finishedMs ?? elapsed;

  return (
    <ActivityChrome
      deckId={deck.id}
      deckTitle={deck.title}
      activity="match"
      badge={
        bestMs !== null ? (
          <span className="rounded-full border border-nb-line px-3 py-1 text-meta font-semibold text-nb-body">
            Best {formatMs(bestMs)}
          </span>
        ) : undefined
      }
      progress={
        <span className="font-mono text-display font-semibold tabular-nums text-[var(--ac)]">
          {formatMs(running)}
        </span>
      }
      segments={[
        { value: cleared.length, tone: "ok" },
        { value: cards.length - cleared.length, tone: "track" },
      ]}
      caption={[
        `${cleared.length} of ${cards.length} paired`,
        // Only worth saying while it is still true.
        bestMs !== null && finishedMs === null && running < bestMs
          ? `beating your best by ${formatMs(bestMs - running)}`
          : null,
        "nothing here touches your schedule",
      ]
        .filter(Boolean)
        .join(" · ")}
    >
      {finishedMs !== null ? (
        <div className="rounded-card border border-nb-line bg-nb-panel p-6">
          <p className="text-label font-semibold tracking-[0.06em] text-ok">
            {bestMs !== null && finishedMs <= bestMs ? "New best" : "Finished"}
          </p>
          <p className="mt-1 text-title font-semibold text-nb-ink">
            {cards.length} pairs in {formatMs(finishedMs)}
          </p>
          {bestMs !== null && (
            <p className="mt-1 text-body text-nb-body">Best {formatMs(bestMs)}</p>
          )}
          <button
            type="button"
            onClick={replay}
            className="mt-5 rounded-ctl bg-[var(--ac)] px-4 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
          >
            Play again
          </button>
        </div>
      ) : (
        <>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {tiles.map((tile) => {
              const done = cleared.includes(tile.ref);
              const isSelected = selected?.id === tile.id;
              return (
                <li key={tile.id}>
                  <button
                    type="button"
                    disabled={done}
                    onClick={() => choose(tile)}
                    className={`flex h-[6.5rem] w-full items-center justify-center overflow-auto rounded-tile border p-2.5 text-center text-body transition-colors ${
                      done
                        ? "border-dashed border-nb-track"
                        : wrong === tile.id
                          ? "border-danger bg-nb-dangerBg text-danger"
                          : isSelected
                            ? "border-[var(--ac)] bg-nb-acBg text-nb-ink"
                            : "border-nb-line bg-nb-panel text-nb-ink hover:border-nb-lineHi"
                    }`}
                  >
                    {/* `invisible` rather than unmounted: a cleared tile keeps
                        its space, so the board does not reflow under the
                        pointer mid-round. */}
                    <span className={done ? "invisible" : ""}>
                      <Markdown text={tile.text} className="text-body" />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="mt-4 text-label text-nb-faint">Click a term, then its definition.</p>
        </>
      )}
    </ActivityChrome>
  );
}
