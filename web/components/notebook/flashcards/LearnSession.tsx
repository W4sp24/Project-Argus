"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import Markdown from "@/components/Markdown";
import ActivityChrome from "@/components/notebook/flashcards/ActivityChrome";
import Button from "@/components/ui/Button";
import { gradeFlashcard, type FlashcardCard, type FlashcardDeckDetail, type FlashcardGrade } from "@/lib/api";
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

  async function settle(result: "correct" | "close" | "wrong") {
    if (!current) return;
    setVerdict(result);
    const grade = gradeFor(result);
    try {
      await gradeFlashcard(deck.id, current.ref, grade);
    } catch {
      // A failed post must not strand the session: the card still advances,
      // and the schedule simply does not learn from this answer.
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

  if (pool.length === 0) {
    return (
      <ActivityChrome deckId={deck.id} deckTitle={deck.title} activity="learn">
        <p className="text-body text-ink-faint">This deck has no cards to learn yet.</p>
      </ActivityChrome>
    );
  }

  if (!current) {
    return (
      <ActivityChrome deckId={deck.id} deckTitle={deck.title} activity="learn">
        <div className="border border-line p-5">
          <p className="font-mono text-label uppercase tracking-[0.16em] text-[var(--ac)]">
            {remaining === 0 ? "deck mastered" : `round ${round} complete`}
          </p>
          <p className="mt-2 text-lead text-ink-bright">
            {mastered.length} of {pool.length} mastered
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {remaining > 0 && <Button onClick={nextRound}>NEXT ROUND</Button>}
            <Link
              href={`/notebook/flashcards/${deck.id}`}
              className="border border-line px-3 py-2 font-mono text-label uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
            >
              back to the deck
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
      activity={`learn · round ${round}`}
      progress={`${mastered.length} / ${pool.length} mastered`}
    >
      <div className="border border-line bg-sunken p-5">
        <p className="mb-1 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
          {stage === "choice" ? "choose the answer" : "type the answer"}
        </p>
        <div className="text-lead text-ink-bright">
          <Markdown text={current.front} className="text-lead" />
        </div>

        {current.hint && !usedHint && verdict === null && (
          <button
            type="button"
            onClick={() => setUsedHint(true)}
            className="mt-3 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint underline underline-offset-2 transition-colors hover:text-ink"
          >
            get a hint
          </button>
        )}
        {usedHint && current.hint && (
          <p className="mt-3 font-mono text-meta text-ink-muted">
            <span className="text-[var(--ac)]">hint</span> :: {current.hint}
          </p>
        )}
      </div>

      {verdict === null ? (
        stage === "choice" ? (
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {options.map((option) => (
              <li key={option.ref}>
                <button
                  type="button"
                  onClick={() =>
                    void settle(option.ref === current.ref ? "correct" : "wrong")
                  }
                  className="w-full border border-line px-3 py-3 text-left text-body text-ink transition-colors hover:border-[var(--ac)]"
                >
                  <Markdown text={option.back} className="text-body" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <form
            className="mt-3 flex flex-wrap gap-2"
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
              className="min-h-9 min-w-0 flex-1 border border-line bg-sunken px-2 py-1.5 font-body text-body text-ink focus:border-lineHi"
            />
            <Button type="submit">ANSWER</Button>
          </form>
        )
      ) : (
        <div className="mt-3 border border-line p-4">
          <p
            className={`font-mono text-label uppercase tracking-[0.14em] ${
              verdict === "wrong" ? "text-danger" : verdict === "close" ? "text-warn" : "text-ok"
            }`}
          >
            {verdict === "correct" ? "correct" : verdict === "close" ? "close enough" : "not quite"}
          </p>
          <div className="mt-2 text-body text-ink-bright">
            <Markdown text={current.back} className="text-body" />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={next}>CONTINUE</Button>
            {verdict === "wrong" && !overrode && (
              // Promotes to `hard`, never to `good`: a card you had to argue
              // for is not a card you knew.
              <Button
                variant="quiet"
                onClick={() => {
                  setOverrode(true);
                  void gradeFlashcard(deck.id, current.ref, "hard").catch(() => {});
                }}
              >
                I WAS RIGHT
              </Button>
            )}
            {overrode && (
              <span className="self-center font-mono text-meta text-ink-faint">
                counted as a near miss
              </span>
            )}
          </div>
        </div>
      )}
    </ActivityChrome>
  );
}
