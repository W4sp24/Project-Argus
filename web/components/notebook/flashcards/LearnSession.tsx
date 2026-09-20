"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import Markdown from "@/components/Markdown";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
import { gradeFlashcard, type FlashcardCard, type FlashcardDeckDetail, type FlashcardGrade } from "@/lib/api";
import { diffWords } from "@/lib/flashcards/diff";
import { canAskMultipleChoice, pickDistractors, shuffle } from "@/lib/flashcards/distractors";
import { judge } from "@/lib/flashcards/matching";

/** Cards per round. Small enough that a round is finishable in a sitting. */
const ROUND = 7;

/** How many times a card must be answered right before it counts as mastered. */
const MASTERY = 2;

type Stage = "choice" | "typed";

/**
 * Adaptive practice, feeding the same schedule REVIEW does.
 *
 * The escalation is per card, not per session: a card you have never answered
 * is asked as multiple choice, and once you have got it right it is asked as
 * a typed answer — recognition first, recall second, which is the order the
 * evidence for testing effects actually supports.
 *
 * **Outcome mapping** is the part worth stating, because it is what keeps this
 * mode honest against REVIEW:
 *
 *   - right first time            → `good`
 *   - close, hinted, or overridden → `hard`
 *   - wrong                        → `again`
 *
 * "I was right" promotes `wrong` to `hard`, never to `good`. A card you had to
 * argue for is not a card you knew, and letting the override reach `good`
 * would make the button a way to launder a miss into a long interval.
 *
 * Everything runs client-side; only the resulting grade is posted, once per
 * card, when the card leaves the round.
 */
export default function LearnSession({ deck }: { deck: FlashcardDeckDetail }) {
  const pool = useMemo(() => deck.card_list.filter((card) => !card.suspended), [deck.card_list]);

  const [correctCount, setCorrectCount] = useState<Record<string, number>>({});
  const [queue, setQueue] = useState<string[]>(() =>
    shuffle(pool.map((card) => card.ref)).slice(0, ROUND),
  );
  const [typed, setTyped] = useState("");
  const [verdict, setVerdict] = useState<"correct" | "close" | "wrong" | null>(null);
  const [usedHint, setUsedHint] = useState(false);
  const [overrode, setOverrode] = useState(false);
  // A ref, not state: it has to be readable and writable synchronously
  // inside one click handler, before React has committed anything.
  const gradingRef = useRef<string | null>(null);
  const [mastered, setMastered] = useState<string[]>([]);
  const [round, setRound] = useState(1);

  const byRef = useMemo(() => new Map(pool.map((card) => [card.ref, card])), [pool]);
  const current: FlashcardCard | undefined = queue[0] ? byRef.get(queue[0]) : undefined;

  const seen = current ? (correctCount[current.ref] ?? 0) : 0;
  const stage: Stage =
    current && seen === 0 && canAskMultipleChoice(pool, current) ? "choice" : "typed";

  // Recomputed only when the card changes, so options do not reshuffle under
  // the pointer on every keystroke.
  const options = useMemo(() => {
    if (!current || stage !== "choice") return [];
    return shuffle([current, ...pickDistractors(pool, current, 3)]);
  }, [current, pool, stage]);

  function gradeFor(result: "correct" | "close" | "wrong"): FlashcardGrade {
    if (result === "wrong") return "again";
    if (result === "close" || usedHint || overrode || seen > 0) return "hard";
    return "good";
  }

  /**
   * One grade per answer.
   *
   * The four choice buttons are all live while a grade is in flight, so a
   * fast double-tap posted two grades for one card. FSRS derives a card's
   * state from its newest review, so the second scheduled from the first and
   * the card advanced twice as far as one answer earned. `grading` closes
   * that window here; `store.grade_card` closes it on the server too, because
   * two windows can do the same thing.
   */
  async function settle(result: "correct" | "close" | "wrong") {
    if (!current || gradingRef.current) return;
    gradingRef.current = current.ref;
    setVerdict(result);
    const grade = gradeFor(result);
    try {
      await gradeFlashcard(deck.id, current.ref, grade);
    } catch {
      // A failed post must not strand the session: the card still advances,
      // and the schedule simply does not learn from this answer.
    } finally {
      gradingRef.current = null;
    }
  }

  function next() {
    if (!current) return;
    const right = verdict === "correct" || verdict === "close" || overrode;
    const count = (correctCount[current.ref] ?? 0) + (right ? 1 : 0);
    setCorrectCount((prev) => ({ ...prev, [current.ref]: right ? count : 0 }));

    const done = right && count >= MASTERY;
    if (done) setMastered((prev) => [...prev, current.ref]);

    setQueue((prev) => (done ? prev.slice(1) : [...prev.slice(1), current.ref]));
    setTyped("");
    setVerdict(null);
    setUsedHint(false);
    setOverrode(false);
  }

  function nextRound() {
    const remaining = pool
      .map((card) => card.ref)
      .filter((ref) => !mastered.includes(ref));
    setQueue(shuffle(remaining).slice(0, ROUND));
    setRound((value) => value + 1);
  }

  const remaining = pool.filter((card) => !mastered.includes(card.ref)).length;

  // Recomputed only when the answer settles, not on every keystroke.
  const diff = useMemo(
    () => diffWords(typed, current?.back ?? ""),
    [typed, current?.back],
  );

  if (pool.length === 0) {
    return (
      <ActivityChrome deckId={deck.id} deckTitle={deck.title} activity="learn">
        <p className="text-body text-nb-faint">This deck has no cards to learn yet.</p>
      </ActivityChrome>
    );
  }

  // Cards you have answered right once but not yet twice — the middle state the
  // bar would otherwise hide inside "remaining".
  const inProgress = pool.filter(
    (card) => !mastered.includes(card.ref) && (correctCount[card.ref] ?? 0) > 0,
  ).length;

  const segments = [
    { value: mastered.length, tone: "ok" as const },
    { value: inProgress, tone: "warn" as const },
    { value: remaining - inProgress, tone: "track" as const },
  ];

  if (!current) {
    return (
      <ActivityChrome deckId={deck.id} deckTitle={deck.title} activity="learn" segments={segments}>
        <div className="rounded-card border border-nb-line bg-nb-panel p-6">
          <p className="text-label font-semibold tracking-[0.06em] text-ok">
            {remaining === 0 ? "Deck mastered" : `Round ${round} complete`}
          </p>
          <p className="mt-1 text-title font-semibold text-nb-ink">
            {mastered.length} of {pool.length} mastered
          </p>
          <div className="mt-5 flex flex-wrap gap-2.5">
            {remaining > 0 && (
              <button
                type="button"
                onClick={nextRound}
                className="rounded-ctl bg-[var(--ac)] px-4 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
              >
                Next round
              </button>
            )}
            <Link
              href={`/notebook/flashcards/${deck.id}`}
              className="rounded-ctl border border-nb-line px-4 py-3 text-body text-nb-ink transition-colors hover:border-nb-lineHi"
            >
              Back to the deck
            </Link>
          </div>
        </div>
      </ActivityChrome>
    );
  }

  return (
    <ActivityChrome
      deckId={deck.id}
      deckTitle={deck.title}
      activity="learn"
      badge={<span className="text-label text-nb-faint">Round {round}</span>}
      progress={
        <>
          {mastered.length}
          <span className="text-nb-faint"> / {pool.length} mastered</span>
        </>
      }
      segments={segments}
    >
      <div className="rounded-card border border-nb-line bg-nb-panel p-6">
        <p className="mb-2 text-label text-nb-faint">
          {stage === "choice" ? "Choose the answer" : "Type the answer"}
        </p>
        <div className="text-nb-ink">
          <Markdown text={current.front} className="text-lead font-medium" />
        </div>

        {current.hint && !usedHint && verdict === null && (
          <button
            type="button"
            onClick={() => setUsedHint(true)}
            className="mt-3.5 text-label text-nb-faint underline underline-offset-2 transition-colors hover:text-nb-ink"
          >
            Get a hint · a hint caps this card at a near miss
          </button>
        )}
        {usedHint && current.hint && (
          <p className="mt-3.5 text-ctl text-nb-body">Hint: {current.hint}</p>
        )}
      </div>

      {verdict === null ? (
        stage === "choice" ? (
          <ul className="mt-3 grid gap-2.5 sm:grid-cols-2">
            {options.map((option) => (
              <li key={option.ref}>
                <button
                  type="button"
                  onClick={() => void settle(option.ref === current.ref ? "correct" : "wrong")}
                  className="w-full rounded-tile border border-nb-line bg-nb-panel px-4 py-3.5 text-left text-nb-ink transition-colors hover:border-[var(--ac)]"
                >
                  <Markdown text={option.back} className="text-body" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <form
            className="mt-3 flex flex-wrap gap-2.5"
            onSubmit={(event) => {
              event.preventDefault();
              void settle(judge(current.back, typed));
            }}
          >
            <input
              autoFocus
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              aria-label="Your answer"
              placeholder="type what you remember"
              className="min-h-12 min-w-0 flex-1 rounded-ctl border border-nb-lineHi bg-nb-panel px-3.5 py-3 text-read text-nb-ink placeholder:text-nb-faint focus:border-[var(--ac)]"
            />
            <button
              type="submit"
              className="rounded-ctl bg-[var(--ac)] px-5 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
            >
              Answer
            </button>
          </form>
        )
      ) : (
        <div
          className={`mt-3 rounded-card border bg-nb-panel p-6 ${
            verdict === "wrong"
              ? "border-nb-dangerLine"
              : verdict === "close"
                ? "border-nb-warnLine"
                : "border-nb-okLine"
          }`}
        >
          <span
            className={`inline-flex rounded-[0.375rem] px-2.5 py-1 text-meta font-semibold ${
              verdict === "wrong"
                ? "bg-nb-dangerBg text-danger"
                : verdict === "close"
                  ? "bg-nb-warnBg text-warn"
                  : "bg-nb-okBg text-ok"
            }`}
          >
            {verdict === "correct" ? "✓ Correct" : verdict === "close" ? "≈ Close enough" : "✗ Not this time"}
          </span>

          {/* The diff only earns its place when you typed something and it was
              not exactly right. On a multiple-choice miss there is nothing to
              compare, and on an exact match there is nothing to show. */}
          {stage === "typed" && typed.trim() && verdict !== "correct" ? (
            <>
              <p className="mb-1.5 mt-4 text-label text-nb-faint">You typed</p>
              <p className="text-read text-nb-body">
                {diff.typed.map((span, index) => (
                  <span
                    key={index}
                    className={span.changed ? "bg-nb-warnBg px-0.5 text-warn" : undefined}
                  >
                    {span.text}
                  </span>
                ))}
              </p>
              <p className="mb-1.5 mt-4 text-label text-nb-faint">The card says</p>
              <p className="text-read text-nb-ink">
                {diff.expected.map((span, index) => (
                  <span
                    key={index}
                    className={span.changed ? "bg-nb-okBg px-0.5 text-ok" : undefined}
                  >
                    {span.text}
                  </span>
                ))}
              </p>
            </>
          ) : (
            <div className="mt-4 text-nb-ink">
              <Markdown text={current.back} className="text-read" />
            </div>
          )}

          {(verdict === "close" || usedHint || overrode) && (
            <p className="mt-4 text-body text-nb-body">
              Counted as a near miss — a card you had to argue for is not a card you knew.
            </p>
          )}

          <div className="mt-4 flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={next}
              className="flex-1 rounded-ctl bg-[var(--ac)] px-4 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90"
            >
              Continue
            </button>
            {verdict === "wrong" && !overrode && (
              // Promotes to `hard`, never to `good`: a card you had to argue
              // for is not a card you knew.
              <button
                type="button"
                onClick={() => {
                  // Once. The button re-renders away on `overrode`, but a
                  // double-click lands both handlers before React commits —
                  // and this card has already been graded by `settle`, so a
                  // second post is a third review for one answer.
                  if (overrode || gradingRef.current) return;
                  setOverrode(true);
                  gradingRef.current = current.ref;
                  void gradeFlashcard(deck.id, current.ref, "hard")
                    .catch(() => {})
                    .finally(() => {
                      gradingRef.current = null;
                    });
                }}
                className="rounded-ctl border border-nb-line px-4 py-3 text-ctl text-nb-body transition-colors hover:border-nb-lineHi hover:text-nb-ink"
              >
                Mark as correct
              </button>
            )}
          </div>
        </div>
      )}
    </ActivityChrome>
  );
}
