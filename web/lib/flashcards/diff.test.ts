import { describe, expect, it } from "vitest";
import { diffWords } from "./diff";

/** The visible result, as the component renders it. */
const render = (spans: { text: string; changed: boolean }[]) =>
  spans.map((span) => (span.changed ? `[${span.text}]` : span.text)).join("");

/** Joining the spans must reproduce the input exactly, or the diff is showing
 *  the reader a different string from the one they typed. */
const rejoin = (spans: { text: string }[]) => spans.map((span) => span.text).join("");

describe("diffWords", () => {
  it("highlights nothing when the answers match", () => {
    const diff = diffWords("the result of each subproblem", "the result of each subproblem");
    expect(diff.typed.every((span) => !span.changed)).toBe(true);
    expect(diff.expected.every((span) => !span.changed)).toBe(true);
  });

  it("highlights only the tail you stopped short of", () => {
    // The artboard's own example. It draws "subproblem" highlighted on both
    // sides, but that is an anchor rather than a difference: everything typed
    // here was right, and the only divergence is what came after. Marking a
    // word you got right is the diff telling you that you were wrong about it.
    const diff = diffWords(
      "the result of each subproblem",
      "the result of each subproblem, keyed by its arguments",
    );
    expect(render(diff.typed)).toBe("the result of each subproblem");
    expect(render(diff.expected)).toBe("the result of each subproblem, [keyed by its arguments]");
  });

  it("highlights a middle that differs, keeping both ends plain", () => {
    const diff = diffWords("a locally best choice always wins", "a locally optimal choice wins");
    expect(render(diff.typed)).toBe("a locally [best choice always ]wins");
    expect(render(diff.expected)).toBe("a locally [optimal choice ]wins");
  });

  it("ignores case and trailing punctuation", () => {
    // "Subproblem." and "subproblem" are the same word; marking a full stop as
    // a difference is noise.
    const diff = diffWords("Top-down caching.", "top-down caching");
    expect(diff.typed.every((span) => !span.changed)).toBe(true);
  });

  it("marks the whole of both when nothing is shared", () => {
    const diff = diffWords("completely wrong", "entirely different");
    expect(render(diff.typed)).toBe("[completely wrong]");
    expect(render(diff.expected)).toBe("[entirely different]");
  });

  it("handles an empty answer", () => {
    const diff = diffWords("", "bottom-up table fill");
    expect(diff.typed).toEqual([]);
    expect(render(diff.expected)).toBe("[bottom-up table fill]");
  });

  it("reproduces both inputs exactly, whitespace included", () => {
    const typed = "the  result of each subproblem";
    const expected = "the result of  each subproblem, keyed by its arguments";
    const diff = diffWords(typed, expected);
    // `tokenize` keeps the whitespace that followed each word, and leading
    // whitespace is not captured — so compare against the trimmed-left input.
    expect(rejoin(diff.typed)).toBe(typed);
    expect(rejoin(diff.expected)).toBe(expected);
  });

  it("does not run off the end when one side is a prefix of the other", () => {
    // head and tail must not overlap, or a span would be counted twice.
    const diff = diffWords("alpha beta", "alpha beta gamma delta");
    expect(rejoin(diff.typed)).toBe("alpha beta");
    expect(render(diff.expected)).toBe("alpha beta [gamma delta]");
  });
});
