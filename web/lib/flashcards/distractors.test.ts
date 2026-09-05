import { describe, expect, it } from "vitest";
import { canAskMultipleChoice, pickDistractors, shuffle } from "./distractors";

const deck = [
  { ref: "a", back: "alpha" },
  { ref: "b", back: "bravo" },
  { ref: "c", back: "charlie" },
  { ref: "d", back: "delta" },
  { ref: "e", back: "echo" },
];

describe("pickDistractors", () => {
  it("never returns the correct card", () => {
    for (let run = 0; run < 50; run += 1) {
      const picked = pickDistractors(deck, deck[0], 3);
      expect(picked.map((card) => card.ref)).not.toContain("a");
    }
  });

  it("returns the requested count when the deck is large enough", () => {
    expect(pickDistractors(deck, deck[0], 3)).toHaveLength(3);
  });

  it("returns fewer, without repeating, when the deck is small", () => {
    const tiny = [deck[0], deck[1]];
    const picked = pickDistractors(tiny, tiny[0], 3);
    expect(picked).toHaveLength(1);
    expect(new Set(picked.map((card) => card.ref)).size).toBe(1);
  });

  it("returns none for a one-card deck", () => {
    expect(pickDistractors([deck[0]], deck[0], 3)).toEqual([]);
  });

  it("never offers the same text twice", () => {
    // A duplicate option is not a distractor, it is a hint: a reader can
    // eliminate both without knowing the answer.
    const dupes = [
      { ref: "a", back: "alpha" },
      { ref: "b", back: "same" },
      { ref: "c", back: "SAME " },
      { ref: "d", back: "other" },
    ];
    const picked = pickDistractors(dupes, dupes[0], 3);
    expect(picked).toHaveLength(2);
  });

  it("never offers an option identical to the correct answer", () => {
    const collide = [
      { ref: "a", back: "alpha" },
      { ref: "b", back: "alpha" },
      { ref: "c", back: "bravo" },
    ];
    const picked = pickDistractors(collide, collide[0], 3);
    expect(picked.map((card) => card.back)).toEqual(["bravo"]);
  });
});

describe("canAskMultipleChoice", () => {
  it("is true when there are enough plausible wrong answers", () => {
    expect(canAskMultipleChoice(deck, deck[0])).toBe(true);
  });

  it("is false with one distractor — a coin flip is not a question", () => {
    expect(canAskMultipleChoice([deck[0], deck[1]], deck[0])).toBe(false);
  });

  it("is false for a one-card deck", () => {
    expect(canAskMultipleChoice([deck[0]], deck[0])).toBe(false);
  });
});

describe("shuffle", () => {
  it("keeps every element exactly once", () => {
    const out = shuffle([1, 2, 3, 4, 5]);
    expect([...out].sort()).toEqual([1, 2, 3, 4, 5]);
  });

  it("does not mutate its input", () => {
    const input = [1, 2, 3];
    shuffle(input);
    expect(input).toEqual([1, 2, 3]);
  });

  it("actually permutes", () => {
    // A shuffle that always returns the input order would pass every test
    // above. Over 200 runs of a 6-element array, an identity result every time
    // is astronomically unlikely.
    const identity = [0, 1, 2, 3, 4, 5];
    let moved = false;
    for (let run = 0; run < 200 && !moved; run += 1) {
      moved = shuffle(identity).some((value, index) => value !== identity[index]);
    }
    expect(moved).toBe(true);
  });
});
