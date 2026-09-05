/**
 * Judging a typed answer.
 *
 * The hard part of a typed-answer mode is not the typing, it is being fair.
 * Too strict and a missing accent or a trailing full stop reads as ignorance;
 * too lenient and "cat" passes for "bat" and the schedule learns a lie.
 *
 * So: normalise away everything that is not knowledge — case, surrounding and
 * internal whitespace, terminal punctuation, diacritics — then require either
 * an exact match or a high similarity. A near miss is `close`, not `correct`:
 * it is accepted, but graded `hard`, because a card you fumbled is not a card
 * you knew.
 */

/** Normalised Levenshtein similarity at or above which a near miss is accepted. */
export const CLOSE_ENOUGH = 0.85;

export type Verdict = "correct" | "close" | "wrong";

/**
 * Strip everything that is not knowledge.
 *
 * NFD + combining-mark removal handles diacritics, because a learner may have
 * no way to type "café" on the keyboard in front of them.
 */
export function normalise(value: string): string {
  return (
    value
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      // Whitespace first, then terminal punctuation. The other order is not
      // idempotent: "LAIT. " ends in a space, so the full stop survives the
      // strip and only disappears on a second pass -- and a normaliser whose
      // output is not a fixed point cannot be reasoned about.
      .replace(/\s+/g, " ")
      .trim()
      .replace(/[.,;:!?]+$/g, "")
      .trim()
  );
}

/**
 * Damerau-Levenshtein distance (optimal string alignment), no dependency.
 *
 * Damerau rather than plain Levenshtein because a **transposition counts as
 * one edit, not two**. Swapping two letters is the single commonest typing
 * error, and plain Levenshtein charges it double: "mitochondira" scores 0.833
 * against "mitochondria" and lands under any threshold strict enough to keep
 * "cat" from matching "bat". As one edit it scores 0.917 and is correctly read
 * as a typo. A test asserting exactly that caught it.
 */
function distance(a: string, b: string): number {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;

  // Three rows: the transposition case needs the one before last.
  let twoBack: number[] = [];
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let best = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        best = Math.min(best, twoBack[j - 2] + 1);
      }
      current[j] = best;
    }
    twoBack = previous;
    previous = current;
  }
  return previous[b.length];
}

/** 1 for identical, 0 for nothing in common. */
export function similarity(a: string, b: string): number {
  const longest = Math.max(a.length, b.length);
  return longest === 0 ? 1 : 1 - distance(a, b) / longest;
}

/**
 * Grade one typed answer.
 *
 * An empty answer short-circuits to `wrong` before similarity is computed —
 * otherwise a one-character expected answer would score 0.5 against nothing
 * and creep toward acceptance.
 */
export function judge(expected: string, actual: string): Verdict {
  const want = normalise(expected);
  const got = normalise(actual);
  if (got.length === 0) return "wrong";
  if (want === got) return "correct";
  return similarity(want, got) >= CLOSE_ENOUGH ? "close" : "wrong";
}
