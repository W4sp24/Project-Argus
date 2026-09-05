"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
import CardFace from "@/components/notebook/flashcards/CardFace";
import Button from "@/components/ui/Button";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { updateCard, type FlashcardCard, type FlashcardDeckDetail } from "@/lib/api";

const FILTERS = ["all", "starred"] as const;
const FILTER_LABELS = { all: "ALL", starred: "★ STARRED" } as const;
type Filter = (typeof FILTERS)[number];

/** Fisher–Yates. `sort(() => Math.random() - 0.5)` is not a shuffle. */
function shuffled<T>(items: T[]): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * Flip through a deck without spending it.
 *
 * This mode exists precisely so that REVIEW can be trusted. Skimming a deck
 * before a lecture must not rewrite a schedule built over weeks, so **nothing
 * here posts a grade**. The ✗/✓ sort is two `useState` piles and dies with the
 * session; only the star, which is a property of the card rather than of this
 * run, is persisted.
 *
 * That split is Quizlet's, and it is the right one: "am I getting this right
 * today" and "when should I see this again" are different questions.
 */
export default function BrowseSession({
  deck,
  onStarred,
}: {
  deck: FlashcardDeckDetail;
  onStarred: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [order, setOrder] = useState<string[] | null>(null);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [learning, setLearning] = useState<string[]>([]);
  const [known, setKnown] = useState<string[]>([]);
  const [round, setRound] = useState(1);

  const pool = useMemo(() => {
    const base = deck.card_list.filter((card) => (filter === "starred" ? card.starred : true));
    if (order === null) return base;
    const byRef = new Map(base.map((card) => [card.ref, card]));
    return order.map((ref) => byRef.get(ref)).filter((card): card is FlashcardCard => !!card);
  }, [deck.card_list, filter, order]);

  const current = pool[index];
  const atEnd = index >= pool.length;

  const advance = useCallback(
    (pile?: "learning" | "known") => {
      if (!current) return;
      if (pile === "learning") setLearning((prev) => [...prev, current.ref]);
      if (pile === "known") setKnown((prev) => [...prev, current.ref]);
      setFlipped(false);
      setIndex((value) => value + 1);
    },
    [current],
  );

  const back = useCallback(() => {
    setFlipped(false);
    setIndex((value) => Math.max(0, value - 1));
  }, []);

  /** Undo the last sort: drop it from its pile and step back onto it. */
  const undo = useCallback(() => {
    if (index === 0) return;
    const previous = pool[index - 1];
    if (previous) {
      setLearning((prev) => prev.filter((ref) => ref !== previous.ref));
      setKnown((prev) => prev.filter((ref) => ref !== previous.ref));
    }
    back();
  }, [back, index, pool]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        setFlipped((value) => !value);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        advance(tracking ? "known" : undefined);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (tracking) advance("learning");
        else back();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, back, tracking]);

  /** A second pass over just the ✗ pile — the reason to sort at all. */
  function reviewStillLearning() {
    setOrder(learning);
    setLearning([]);
    setKnown([]);
    setIndex(0);
    setRound((value) => value + 1);
  }

  function restart(shuffle: boolean) {
    setOrder(shuffle ? shuffled(pool.map((card) => card.ref)) : null);
    setLearning([]);
    setKnown([]);
    setIndex(0);
    setRound(1);
    setFlipped(false);
  }

  async function toggleStar() {
    if (!current) return;
    await updateCard(deck.id, current.ref, { starred: !current.starred });
    onStarred();
  }

  return (
    <ActivityChrome
      deckId={deck.id}
      deckTitle={deck.title}
      activity={round === 1 ? "flashcards" : `flashcards · round ${round}`}
      progress={pool.length === 0 ? "0 / 0" : `${Math.min(index + 1, pool.length)} / ${pool.length}`}
      keys="space flip · ← → move · nothing here changes your schedule"
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <SegmentedControl
          options={FILTERS}
          labels={FILTER_LABELS}
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setOrder(null);
            setIndex(0);
            setLearning([]);
            setKnown([]);
            setRound(1);
          }}
        />
        <label className="flex items-center gap-2 font-mono text-label uppercase tracking-[0.12em] text-ink-muted">
          <input
            type="checkbox"
            checked={tracking}
            onChange={(event) => setTracking(event.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--ac)]"
          />
          Track progress
        </label>
        <Button variant="quiet" onClick={() => restart(true)}>
          ⇄ SHUFFLE
        </Button>
      </div>

      {pool.length === 0 ? (
        <p className="text-body text-ink-faint">
          {filter === "starred" ? "No starred cards in this deck yet." : "This deck has no cards."}
        </p>
      ) : atEnd ? (
        <div className="border border-line p-5">
          <p className="font-mono text-label uppercase tracking-[0.16em] text-[var(--ac)]">
            end of the deck
          </p>
          {tracking ? (
            <>
              <p className="mt-2 text-lead text-ink-bright">
                {known.length} known · {learning.length} still learning
              </p>
              <p className="mt-1 font-mono text-meta text-ink-faint">
                None of this touched your review schedule.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {learning.length > 0 && (
                  <Button onClick={reviewStillLearning}>
                    REVIEW THE {learning.length} STILL LEARNING
                  </Button>
                )}
                <Button variant="quiet" onClick={() => restart(false)}>
                  START OVER
                </Button>
              </div>
            </>
          ) : (
            <div className="mt-4 flex gap-2">
              <Button variant="quiet" onClick={() => restart(false)}>
                START OVER
              </Button>
              <Button variant="quiet" onClick={() => restart(true)}>
                SHUFFLE AND RESTART
              </Button>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="mb-2 flex items-center justify-end">
            <Button
              variant="quiet"
              aria-label={current.starred ? "Unstar this card" : "Star this card"}
              aria-pressed={current.starred}
              onClick={() => void toggleStar()}
              className={current.starred ? "text-[var(--ac)]" : ""}
            >
              {current.starred ? "★" : "☆"}
            </Button>
          </div>

          <CardFace
            front={current.front}
            back={current.back}
            hint={flipped ? null : current.hint}
            flipped={flipped}
            onFlip={() => setFlipped((value) => !value)}
          />

          <button
            type="button"
            data-testid="flashcard-flip"
            aria-pressed={flipped}
            onClick={() => setFlipped((value) => !value)}
            className="mt-2 w-full border border-line py-2 font-mono text-meta uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
          >
            {flipped ? "SHOW QUESTION" : "SHOW ANSWER"}
          </button>

          <div className="mt-4 flex items-center justify-center gap-3 border-t border-line pt-4">
            {tracking ? (
              <>
                <Button
                  aria-label="Still learning"
                  onClick={() => advance("learning")}
                  className="border-danger text-danger"
                >
                  ✗ STILL LEARNING
                </Button>
                <Button variant="quiet" aria-label="Undo" disabled={index === 0} onClick={undo}>
                  ↺
                </Button>
                <Button aria-label="Know it" onClick={() => advance("known")} className="border-ok text-ok">
                  ✓ KNOW IT
                </Button>
              </>
            ) : (
              <>
                <Button aria-label="Previous card" disabled={index === 0} onClick={back}>
                  ←
                </Button>
                <Button aria-label="Next card" onClick={() => advance()}>
                  →
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </ActivityChrome>
  );
}
