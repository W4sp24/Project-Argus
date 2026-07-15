"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import {
  type Flashcard,
  type Grade,
  GRADE_INTERVAL,
  GRADE_LABEL,
  SEED_CARDS,
} from "@/components/preview/flashcardsMock";

const GRADES: Grade[] = ["again", "hard", "good", "easy"];

const GRADE_STYLE: Record<Grade, string> = {
  again: "hover:border-danger hover:text-danger",
  hard: "hover:border-amber-400 hover:text-amber-400",
  good: "hover:border-[var(--ac)] hover:text-[var(--ac)]",
  easy: "hover:border-ok hover:text-ok",
};

let nextId = 0;

/**
 * /study/flashcards [PREVIEW] (§4, §5, §9 file plan): DECK.MANAGE (left) +
 * STUDY.SESSION (right) share one local card list — all client state, no
 * backend write (§8 flags.flashcards). Flip is a pure CSS transform
 * (globals.css `.flip-card*`); grading advances the session and toasts the
 * mock SRS schedule. Never calls `fetch(` — the honesty-rule grep guard for
 * components/preview/**.
 */
export default function Flashcards() {
  const [cards, setCards] = useState<Flashcard[]>(SEED_CARDS);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const { show } = useToast();

  const current = cards[index % Math.max(cards.length, 1)];

  function addCard(event: React.FormEvent) {
    event.preventDefault();
    const f = front.trim();
    const b = back.trim();
    if (!f || !b) return;
    nextId += 1;
    setCards((prev) => [...prev, { id: `local-${nextId}`, front: f, back: b }]);
    setFront("");
    setBack("");
  }

  function removeCard(id: string) {
    setCards((prev) => {
      const next = prev.filter((card) => card.id !== id);
      if (index >= next.length) setIndex(0);
      return next;
    });
    setFlipped(false);
  }

  function grade(g: Grade) {
    if (cards.length === 0) return;
    show(`scheduled :: ${GRADE_LABEL[g].toLowerCase()} in ${GRADE_INTERVAL[g]}`);
    setFlipped(false);
    setIndex((i) => (cards.length ? (i + 1) % cards.length : 0));
  }

  return (
    <>
      <Panel label="DECK.MANAGE" preview>
        <form onSubmit={addCard} className="mb-4 flex flex-col gap-2 border-b border-line pb-4">
          <input
            value={front}
            onChange={(event) => setFront(event.target.value)}
            placeholder="front"
            className="border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <input
            value={back}
            onChange={(event) => setBack(event.target.value)}
            placeholder="back"
            className="border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <button
            type="submit"
            disabled={!front.trim() || !back.trim()}
            className="self-start border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            + ADD CARD
          </button>
        </form>

        {cards.length === 0 ? (
          <p className="text-[13px] text-ink-faint">No cards — add one above.</p>
        ) : (
          <ul className="space-y-1.5">
            {cards.map((card) => (
              <li
                key={card.id}
                className="group flex items-center justify-between gap-2 border border-line px-3 py-2 transition-colors hover:border-lineHi"
              >
                <span className="min-w-0 truncate text-[13px] text-ink">{card.front}</span>
                <button
                  aria-label={`Delete card: ${card.front}`}
                  onClick={() => removeCard(card.id)}
                  className="shrink-0 font-mono text-xs text-ink-faint transition-colors hover:text-danger"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        label="STUDY.SESSION"
        preview
        headerRight={
          <span className="font-mono text-[10px] text-ink-faint">
            {cards.length ? `${(index % cards.length) + 1} / ${cards.length}` : "0 / 0"}
          </span>
        }
      >
        {!current ? (
          <p className="text-[13px] text-ink-faint">Add a card to start a session.</p>
        ) : (
          <>
            <div className="flip-card h-40">
              {/* Both faces render their own text as the button's accessible
                  name (no aria-label override — a shared label between two
                  buttons would make them ambiguous to assistive tech and to
                  test locators). data-testid carries the structural
                  flip-state signal for e2e, since backface-visibility isn't
                  something visibility-based test assertions can see. */}
              <div data-testid="flashcard-inner" className={`flip-card-inner ${flipped ? "is-flipped" : ""}`}>
                <button
                  type="button"
                  data-testid="flashcard-front"
                  onClick={() => setFlipped((value) => !value)}
                  aria-label={`Flashcard front: ${current.front}. Click to flip.`}
                  className="flip-card-face flip-card-front flex w-full items-center justify-center border border-line bg-sunken p-4 text-center text-[15px] text-ink-bright transition-colors hover:border-lineHi"
                >
                  {current.front}
                </button>
                <button
                  type="button"
                  data-testid="flashcard-back"
                  onClick={() => setFlipped((value) => !value)}
                  aria-label={`Flashcard back: ${current.back}. Click to flip.`}
                  className="flip-card-face flip-card-back flex w-full items-center justify-center border border-[var(--ac)] bg-[var(--ac-bg)] p-4 text-center text-[15px] text-ink-bright"
                >
                  {current.back}
                </button>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-4 gap-1.5 border-t border-line pt-4 font-mono text-[10px] uppercase tracking-[0.12em]">
              {GRADES.map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => grade(g)}
                  className={`border border-line py-2 text-ink-muted transition-colors ${GRADE_STYLE[g]}`}
                >
                  {GRADE_LABEL[g]}
                </button>
              ))}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
