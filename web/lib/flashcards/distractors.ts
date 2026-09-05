/**
 * Building a multiple-choice question out of a deck.
 *
 * The wrong options come from sibling cards in the same deck, which is what
 * makes the question worth answering: four options drawn from the same
 * material are plausible, so getting it right means recognising the answer
 * rather than spotting the one sentence that looks like it belongs.
 */

export interface Choosable {
  ref: string;
  back: string;
}

/** Fisher–Yates. `sort(() => Math.random() - 0.5)` is a biased permutation. */
export function shuffle<T>(items: T[]): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * Up to `count` wrong options for `correct`, drawn from `pool`.
 *
 * Never returns the correct card, and never returns two options that read the
 * same — a duplicate option is not a distractor, it is a hint, since a reader
 * can eliminate both. A small deck simply yields fewer; the caller decides
 * whether that is still a multiple-choice question.
 */
export function pickDistractors<T extends Choosable>(
  pool: T[],
  correct: Choosable,
  count: number,
): T[] {
  const seen = new Set([correct.back.trim().toLowerCase()]);
  const candidates: T[] = [];
  for (const card of shuffle(pool)) {
    if (card.ref === correct.ref) continue;
    const key = card.back.trim().toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push(card);
    if (candidates.length >= count) break;
  }
  return candidates;
}

/**
 * Can this card be asked as multiple choice at all?
 *
 * Fewer than two wrong options is not a question — with one distractor it is a
 * coin flip, and with none it answers itself. The caller falls back to a typed
 * answer, which always works.
 */
export const MIN_DISTRACTORS = 2;

export function canAskMultipleChoice<T extends Choosable>(
  pool: T[],
  correct: Choosable,
): boolean {
  return pickDistractors(pool, correct, MIN_DISTRACTORS).length >= MIN_DISTRACTORS;
}
