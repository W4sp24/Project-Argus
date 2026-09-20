/**
 * A word-level diff of what you typed against what the card says.
 *
 * Learn already decides *whether* an answer counts (`matching.ts::judge`, a
 * Damerau-Levenshtein ratio). This answers the next question, which judging
 * cannot: **where** the two differ. "Close enough" with no indication of what
 * was missing teaches nothing — you are told you were nearly right and left to
 * spot the gap yourself.
 *
 * Deliberately a common-prefix/common-suffix diff rather than a full LCS. The
 * two strings being compared are one short answer against another, and the
 * useful shape is almost always "you stopped early" or "you said this bit
 * differently" — one contiguous region. An LCS would scatter the highlight
 * across several tiny fragments and read as noise for no extra truth.
 */

export interface Span {
  text: string;
  changed: boolean;
}

export interface WordDiff {
  typed: Span[];
  expected: Span[];
}

/** Split into words while keeping the whitespace that followed each one, so
 *  joining the spans back up reproduces the original string exactly. */
function tokenize(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [];
}

/** Compared case- and punctuation-insensitively: "Subproblem." and "subproblem"
 *  are the same word, and highlighting a full stop as a difference is noise. */
function normalise(token: string): string {
  return token.trim().toLowerCase().replace(/[.,;:!?"'()]/g, "");
}

function spans(tokens: string[], from: number, to: number): Span[] {
  const result: Span[] = [];
  const push = (text: string, changed: boolean) => {
    if (!text) return;
    const last = result[result.length - 1];
    if (last && last.changed === changed) last.text += text;
    else result.push({ text, changed });
  };
  tokens.forEach((token, index) => push(token, index >= from && index < to));
  return result;
}

export function diffWords(typed: string, expected: string): WordDiff {
  const a = tokenize(typed);
  const b = tokenize(expected);

  let head = 0;
  while (head < a.length && head < b.length && normalise(a[head]) === normalise(b[head])) head++;

  let tail = 0;
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    normalise(a[a.length - 1 - tail]) === normalise(b[b.length - 1 - tail])
  ) {
    tail++;
  }

  return {
    typed: spans(a, head, a.length - tail),
    expected: spans(b, head, b.length - tail),
  };
}
