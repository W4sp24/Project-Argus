"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import {
  type DueCard,
  type FlashcardGrade,
  gradeFlashcard,
  generateFlashcardDeck,
  useDueCards,
  useFlashcardDecks,
  useStudyCourses,
} from "@/lib/api";

const GRADES: FlashcardGrade[] = ["again", "hard", "good", "easy"];

const GRADE_LABEL: Record<FlashcardGrade, string> = {
  again: "AGAIN",
  hard: "HARD",
  good: "GOOD",
  easy: "EASY",
};

const GRADE_STYLE: Record<FlashcardGrade, string> = {
  again: "hover:border-danger hover:text-danger",
  hard: "hover:border-amber-400 hover:text-amber-400",
  good: "hover:border-[var(--ac)] hover:text-[var(--ac)]",
  easy: "hover:border-ok hover:text-ok",
};

/**
 * /study/flashcards (§4, §5, §9 file plan): DECK.MANAGE (left) generates a
 * deck by parsing `Q:: A::` pairs from `15-Courses/<CODE>/flashcards.md`
 * (`POST /api/flashcards/decks`) and lists prior decks. STUDY.SESSION
 * (right) pulls the real due queue for the selected deck
 * (`GET /api/flashcards/decks/{id}/due`) and grades cards against a real
 * FSRS scheduler (`POST /api/flashcards/decks/{id}/cards/{cardId}/grade`,
 * backend/flashcards.py) — no more mock SRS math or local-only state
 * (flags.flashcards: enabled).
 */
export default function Flashcards() {
  const { data: courses } = useStudyCourses();
  const [genCourse, setGenCourse] = useState("");
  const [generating, setGenerating] = useState(false);
  const { show } = useToast();

  const { data: decks, mutate: refreshDecks } = useFlashcardDecks();
  const [deckId, setDeckId] = useState<number | null>(null);

  useEffect(() => {
    if (deckId === null && decks && decks.length > 0) setDeckId(decks[0].id);
  }, [decks, deckId]);

  const { data: due, mutate: refreshDue } = useDueCards(deckId);
  const [queue, setQueue] = useState<DueCard[]>([]);
  const [flipped, setFlipped] = useState(false);
  const [grading, setGrading] = useState(false);

  useEffect(() => {
    setQueue(due ?? []);
    setFlipped(false);
  }, [due]);

  const current = queue[0];
  const selectedDeck = decks?.find((deck) => deck.id === deckId) ?? null;

  async function generateDeck(event: React.FormEvent) {
    event.preventDefault();
    if (!genCourse) return;
    setGenerating(true);
    try {
      const deck = await generateFlashcardDeck(genCourse);
      show(`deck ready :: ${deck.course} — ${deck.cards} cards`);
      await refreshDecks();
      setDeckId(deck.id);
    } catch (error) {
      show(`deck generation failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setGenerating(false);
    }
  }

  async function grade(g: FlashcardGrade) {
    if (!current || deckId === null || grading) return;
    setGrading(true);
    try {
      const result = await gradeFlashcard(deckId, current.id, g);
      const until = new Date(result.due_at).toLocaleString();
      show(`scheduled :: ${GRADE_LABEL[g].toLowerCase()} — next due ${until}`);
      setQueue((prev) => prev.slice(1));
      setFlipped(false);
      refreshDue();
    } catch (error) {
      show(`grading failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setGrading(false);
    }
  }

  return (
    <>
      <Panel label="DECK.MANAGE">
        <form onSubmit={generateDeck} className="mb-4 flex flex-wrap items-center gap-2 border-b border-line pb-4">
          <select
            value={genCourse}
            onChange={(event) => setGenCourse(event.target.value)}
            className="border border-line bg-sunken px-2 py-1.5 font-mono text-[12px] text-ink focus:border-lineHi focus:outline-none"
          >
            <option value="">select course…</option>
            {(courses ?? []).map((course) => (
              <option key={course.code} value={course.code}>
                {course.code}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={!genCourse || generating}
            className="border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            {generating ? "PARSING…" : "+ GENERATE DECK"}
          </button>
        </form>

        {!decks || decks.length === 0 ? (
          <p className="text-[13px] text-ink-faint">
            No decks yet — generate one above (needs `flashcards.md` with `Q::`/`A::` pairs in the
            course folder).
          </p>
        ) : (
          <ul className="space-y-1.5">
            {decks.map((deck) => (
              <li key={deck.id}>
                <button
                  onClick={() => setDeckId(deck.id)}
                  className={`flex w-full items-center justify-between gap-2 border px-3 py-2 text-left transition-colors ${
                    deck.id === deckId ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
                  }`}
                >
                  <span className="min-w-0 truncate text-[13px] text-ink">{deck.title}</span>
                  <span className="shrink-0 font-mono text-[11px] text-ink-faint">{deck.cards} cards</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        label="STUDY.SESSION"
        headerRight={
          <span className="font-mono text-[10px] text-ink-faint">
            {selectedDeck ? `${queue.length} due` : "0 due"}
          </span>
        }
      >
        {!selectedDeck ? (
          <p className="text-[13px] text-ink-faint">Generate or select a deck to start a session.</p>
        ) : !current ? (
          <p className="text-[13px] text-ink-faint">No cards due right now — check back later.</p>
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
                  disabled={grading}
                  onClick={() => grade(g)}
                  className={`border border-line py-2 text-ink-muted transition-colors disabled:opacity-40 ${GRADE_STYLE[g]}`}
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
