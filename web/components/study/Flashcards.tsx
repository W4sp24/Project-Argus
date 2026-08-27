"use client";

import { useEffect, useState } from "react";
import Markdown from "@/components/Markdown";
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
 * deck by parsing `Q:: A::` pairs from the course's `flashcards.md`
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
            aria-label="Course"
            className="min-h-9 border border-line bg-sunken px-2 py-1.5 font-mono text-label text-ink focus:border-lineHi"
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
            className="border border-line px-3 py-1.5 font-mono text-label uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            {generating ? "PARSING…" : "+ GENERATE DECK"}
          </button>
        </form>

        {!decks || decks.length === 0 ? (
          <p className="text-body text-ink-faint">
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
                  <span className="min-w-0 truncate text-body text-ink">{deck.title}</span>
                  <span className="shrink-0 font-mono text-label text-ink-faint">{deck.cards} cards</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        label="STUDY.SESSION"
        headerRight={
          <span className="font-mono text-meta text-ink-faint">
            {selectedDeck ? `${queue.length} due` : "0 due"}
          </span>
        }
      >
        {!selectedDeck ? (
          <p className="text-body text-ink-faint">Generate or select a deck to start a session.</p>
        ) : !current ? (
          <p className="text-body text-ink-faint">No cards due right now — check back later.</p>
        ) : (
          <>
            {/* The faces are divs and the flip is one button beneath them.
                They used to be two <button>s, which stopped working the
                moment a face rendered markdown:

                - Each carried an `aria-label` built from the raw card text,
                  and an aria-label overrides descendant content. Once a face
                  is typeset, a screen reader would read the LaTeX source --
                  "dollar backslash frac open brace" -- which is worse than
                  the plain text it replaced. (The comment that used to sit
                  here claimed there was no aria-label override. There was
                  one, four lines below it.)
                - A markdown link inside a <button> is invalid HTML: an
                  interactive element cannot contain another. Firefox
                  activates the button when the link is clicked.
                - Both faces stay mounted (backface-visibility, not
                  display:none), so both were focusable and both were in the
                  accessibility tree, one of them invisible.

                KaTeX marks its own visual tree aria-hidden and exposes MathML
                beside it, so the accessible answer here is to render the
                content and get out of its way.

                data-testid still carries the structural flip-state signal for
                e2e, since backface-visibility is not something a
                visibility-based assertion can see. */}
            <div className="flip-card h-40">
              <div data-testid="flashcard-inner" className={`flip-card-inner ${flipped ? "is-flipped" : ""}`}>
                <div
                  data-testid="flashcard-front"
                  aria-hidden={flipped}
                  {...(flipped ? { inert: true } : {})}
                  className="flip-card-face flip-card-front flex w-full items-center justify-center overflow-auto border border-line bg-sunken p-4 text-center text-lead text-ink-bright"
                >
                  <Markdown text={current.front} className="text-lead" />
                </div>
                <div
                  data-testid="flashcard-back"
                  aria-hidden={!flipped}
                  {...(flipped ? {} : { inert: true })}
                  className="flip-card-face flip-card-back flex w-full items-center justify-center overflow-auto border border-[var(--ac)] bg-[var(--ac-bg)] p-4 text-center text-lead text-ink-bright"
                >
                  <Markdown text={current.back} className="text-lead" />
                </div>
              </div>
            </div>
            <button
              type="button"
              data-testid="flashcard-flip"
              aria-pressed={flipped}
              onClick={() => setFlipped((value) => !value)}
              className="mt-2 w-full border border-line py-2 font-mono text-meta uppercase tracking-[0.12em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
            >
              {flipped ? "SHOW QUESTION" : "SHOW ANSWER"}
            </button>

            <div className="mt-4 grid grid-cols-4 gap-1.5 border-t border-line pt-4 font-mono text-meta uppercase tracking-[0.12em]">
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
