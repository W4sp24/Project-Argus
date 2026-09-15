"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
import CardFace from "@/components/notebook/flashcards/CardFace";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { updateCard, type FlashcardCard, type FlashcardDeckDetail } from "@/lib/api";

const FILTERS = ["all", "starred"] as const;
const FILTER_LABELS = { all: "All", starred: "★ Starred" } as const;
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

  const quiet =
    "rounded-ctl border border-nb-line px-3.5 py-2 text-ctl text-nb-ink transition-colors hover:border-nb-lineHi disabled:opacity-40";

  return (
    <ActivityChrome
      deckId={deck.id}
      deckTitle={deck.title}
      activity="browse"
      badge={round > 1 ? <span className="text-label text-nb-faint">Round {round}</span> : undefined}
      progress={
        pool.length === 0 ? (
          "0 / 0"
        ) : (
          <>
            {Math.min(index + 1, pool.length)}
            <span className="text-nb-faint"> / {pool.length}</span>
          </>
        )
      }
      segments={
        tracking
          ? [
              { value: known.length, tone: "ok" },
              { value: learning.length, tone: "warn" },
              { value: Math.max(0, pool.length - known.length - learning.length), tone: "track" },
            ]
          : [
              { value: index, tone: "ac" },
              { value: Math.max(0, pool.length - index), tone: "track" },
            ]
      }
      caption="Nothing here changes your schedule."
      keys="space flip · ← → move"
    >
      <div className="mb-3.5 flex flex-wrap items-center gap-3">
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
        <label className="flex items-center gap-2 text-ctl text-nb-body">
          <input
            type="checkbox"
            checked={tracking}
            onChange={(event) => setTracking(event.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--ac)]"
          />
          Track progress
        </label>
        <button type="button" onClick={() => restart(true)} className={`${quiet} ml-auto`}>
          ⇄ Shuffle
        </button>
      </div>

      {pool.length === 0 ? (
        <p className="text-body text-nb-faint">
          {filter === "starred" ? "No starred cards in this deck yet." : "This deck has no cards."}
        </p>
      ) : atEnd ? (
        <div className="rounded-card border border-nb-line bg-nb-panel p-6">
          <p className="text-label font-semibold tracking-[0.06em] text-ok">End of the deck</p>
          {tracking ? (
            <>
              <p className="mt-1 text-title font-semibold text-nb-ink">
                {known.length} known · {learning.length} still learning
              </p>
              <p className="mt-1.5 text-body text-nb-body">
                None of this touched your review schedule.
              </p>
              <div className="mt-5 flex flex-wrap gap-2.5">
                {learning.length > 0 && (
                  <button
                    type="button"
                    onClick={reviewStillLearning}
                    className="rounded-ctl bg-[var(--ac)] px-4 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
                  >
                    Review the {learning.length} still learning
                  </button>
                )}
                <button type="button" onClick={() => restart(false)} className={quiet}>
                  Start over
                </button>
              </div>
            </>
          ) : (
            <div className="mt-5 flex flex-wrap gap-2.5">
              <button type="button" onClick={() => restart(false)} className={quiet}>
                Start over
              </button>
              <button type="button" onClick={() => restart(true)} className={quiet}>
                Shuffle and restart
              </button>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="mb-2 flex items-center justify-end">
            <button
              type="button"
              aria-label={current.starred ? "Unstar this card" : "Star this card"}
              aria-pressed={current.starred}
              onClick={() => void toggleStar()}
              className={`px-1.5 ${current.starred ? "text-warn" : "text-nb-faint"}`}
            >
              {current.starred ? "★" : "☆"}
            </button>
          </div>

          <CardFace
            front={current.front}
            back={current.back}
            hint={current.hint}
            flipped={flipped}
            onFlip={() => setFlipped((value) => !value)}
          />

          <button
            type="button"
            data-testid="flashcard-flip"
            aria-pressed={flipped}
            onClick={() => setFlipped((value) => !value)}
            className="mt-3 w-full rounded-ctl bg-[var(--ac)] py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
          >
            {flipped ? "Show question" : "Show answer"}
            <span className="font-normal opacity-60"> · space</span>
          </button>

          <div className="mt-3 flex items-center justify-center gap-2.5">
            {tracking ? (
              <>
                <button
                  type="button"
                  aria-label="Still learning"
                  onClick={() => advance("learning")}
                  className="rounded-ctl border border-nb-dangerLine px-4 py-2.5 text-ctl font-semibold text-danger transition-colors hover:bg-nb-dangerBg"
                >
                  ✗ Still learning
                </button>
                <button
                  type="button"
                  aria-label="Undo"
                  disabled={index === 0}
                  onClick={undo}
                  className={quiet}
                >
                  ↺
                </button>
                <button
                  type="button"
                  aria-label="Know it"
                  onClick={() => advance("known")}
                  className="rounded-ctl border border-nb-okLine px-4 py-2.5 text-ctl font-semibold text-ok transition-colors hover:bg-nb-okBg"
                >
                  ✓ Know it
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  aria-label="Previous card"
                  disabled={index === 0}
                  onClick={back}
                  className={quiet}
                >
                  ←
                </button>
                <button
                  type="button"
                  aria-label="Next card"
                  onClick={() => advance()}
                  className={quiet}
                >
                  →
                </button>
              </>
            )}
          </div>
        </>
      )}
    </ActivityChrome>
  );
}
