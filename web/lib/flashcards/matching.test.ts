import { describe, expect, it } from "vitest";
import { judge, normalise, similarity } from "./matching";

describe("judge", () => {
  it("accepts an exact answer", () => {
    expect(judge("Paris", "Paris")).toBe("correct");
  });

  it("ignores case, surrounding space and terminal punctuation", () => {
    expect(judge("Paris", "  paris ")).toBe("correct");
    expect(judge("Paris", "paris.")).toBe("correct");
    expect(judge("Paris", "PARIS!")).toBe("correct");
  });

  it("ignores diacritics, because a learner may have no way to type them", () => {
    expect(judge("café", "cafe")).toBe("correct");
    expect(judge("naïve", "naive")).toBe("correct");
  });

  it("collapses internal whitespace", () => {
    expect(judge("big O notation", "big   O    notation")).toBe("correct");
  });

  it("calls a transposition close rather than wrong", () => {
    // One transposed letter is a typo, not ignorance. Accepted, graded `hard`.
    expect(judge("mitochondria", "mitochondira")).toBe("close");
  });

  it("calls a genuinely different answer wrong", () => {
    expect(judge("mitochondria", "ribosome")).toBe("wrong");
  });

  it("does not let a short answer pass on similarity alone", () => {
    // "cat" vs "bat" is 0.67 — below threshold, and it must stay that way:
    // short answers are where a lenient matcher does the most damage.
    expect(judge("cat", "bat")).toBe("wrong");
    expect(judge("O(n)", "O(1)")).toBe("wrong");
  });

  it("treats an empty answer as wrong, never as close", () => {
    // Guarded before similarity is computed: a one-character expected answer
    // would otherwise score 0.5 against nothing and creep toward acceptance.
    expect(judge("Paris", "")).toBe("wrong");
    expect(judge("Paris", "   ")).toBe("wrong");
    expect(judge("a", "")).toBe("wrong");
  });

  it("does not accept a right answer buried in a wrong one", () => {
    expect(judge("Paris", "definitely not Paris at all")).toBe("wrong");
  });

  it("accepts a long answer missing one character", () => {
    expect(judge("overlapping subproblems", "overlapping subproblem")).toBe("close");
  });
});

describe("normalise", () => {
  it("is idempotent", () => {
    const once = normalise("  Café,  au   LAIT. ");
    expect(normalise(once)).toBe(once);
  });

  it("keeps punctuation that is not terminal", () => {
    // A definition's internal commas carry meaning; only trailing marks go.
    expect(normalise("a, b, and c.")).toBe("a, b, and c");
  });
});

describe("similarity", () => {
  it("is 1 for identical strings and for two empty ones", () => {
    expect(similarity("abc", "abc")).toBe(1);
    expect(similarity("", "")).toBe(1);
  });

  it("is 0 when nothing matches", () => {
    expect(similarity("abc", "xyz")).toBe(0);
  });

  it("is symmetric", () => {
    expect(similarity("kitten", "sitting")).toBeCloseTo(similarity("sitting", "kitten"), 10);
  });
});
