import { describe, expect, it } from "vitest";
import { detectFormat, parseDelimited, parseQaPairs } from "./parsing";

/**
 * Mirrors `tests/features/flashcards/test_parsing.py` case for case. The
 * import dialog previews what will be created using this parser and the server
 * creates it using that one, so any disagreement is a lie told to the user.
 */
describe("parseDelimited", () => {
  it("splits tab-separated rows", () => {
    expect(parseDelimited("front\tback\nsecond\tpair", "tab", "newline")).toEqual([
      { front: "front", back: "back" },
      { front: "second", back: "pair" },
    ]);
  });

  it("splits comma/semicolon", () => {
    expect(parseDelimited("a,1; b,2", "comma", "semicolon")).toEqual([
      { front: "a", back: "1" },
      { front: "b", back: "2" },
    ]);
  });

  it("splits on the first field delimiter only", () => {
    // A definition legitimately contains commas; splitting greedily would
    // truncate exactly the cards worth writing.
    expect(parseDelimited("term,a, b, and c", "comma", "newline")).toEqual([
      { front: "term", back: "a, b, and c" },
    ]);
  });

  it("keeps a hyphenated answer whole under the dash delimiter", () => {
    expect(parseDelimited("big-O-an upper bound", "dash", "newline")).toEqual([
      { front: "big", back: "O-an upper bound" },
    ]);
  });

  it("drops rows with no delimiter or an empty half", () => {
    expect(parseDelimited("good\tpair\nlonely\n\tnofront\nnoback\t", "tab", "newline")).toEqual([
      { front: "good", back: "pair" },
    ]);
  });

  it("tolerates CRLF", () => {
    expect(parseDelimited("a\tb\r\nc\td", "tab", "newline")).toEqual([
      { front: "a", back: "b" },
      { front: "c", back: "d" },
    ]);
  });

  it("ignores blank rows", () => {
    expect(parseDelimited("a\tb\n\n\nc\td\n", "tab", "newline")).toEqual([
      { front: "a", back: "b" },
      { front: "c", back: "d" },
    ]);
  });

  it("yields nothing for an unknown delimiter rather than throwing", () => {
    expect(parseDelimited("a|b", "pipe", "newline")).toEqual([]);
  });
});

describe("parseQaPairs", () => {
  it("reads a self-test tail out of surrounding prose", () => {
    const note = "# Lecture\n\nProse.\n\n## Self-test\n\nQ:: what is P\nA:: polynomial time\n";
    expect(parseQaPairs(note)).toEqual([{ front: "what is P", back: "polynomial time" }]);
  });

  it("folds continuation lines into the field they belong to", () => {
    const text = "Q:: what is a monad\nreally\nA:: a monoid in the category\nof endofunctors";
    expect(parseQaPairs(text)).toEqual([
      { front: "what is a monad\nreally", back: "a monoid in the category\nof endofunctors" },
    ]);
  });

  it("drops a question with no answer", () => {
    expect(parseQaPairs("Q:: lonely\nQ:: paired\nA:: yes")).toEqual([
      { front: "paired", back: "yes" },
    ]);
  });

  it("tolerates CRLF", () => {
    expect(parseQaPairs("Q:: a\r\nA:: b\r\n")).toEqual([{ front: "a", back: "b" }]);
  });

  it("finds nothing in plain prose", () => {
    expect(parseQaPairs("just some prose")).toEqual([]);
  });
});

describe("detectFormat", () => {
  it("recognises a Q::/A:: body", () => {
    const out = detectFormat("Q:: a\nA:: b");
    expect(out.format).toBe("qa");
    expect(out.cards).toHaveLength(1);
  });

  it("lets Q:: win even when the file also contains tabs", () => {
    // A note with an indented line is prose, not a two-column table. Guessing
    // "delimited" here would split a sentence in half and call it a card.
    const out = detectFormat("Q:: what is P\n\tan indented aside\nA:: polynomial time");
    expect(out.format).toBe("qa");
  });

  it("picks tab over comma when both parse but tab parses more", () => {
    const out = detectFormat("a\tb\nc\td");
    expect(out.format).toBe("delimited");
    expect(out.field).toBe("tab");
    expect(out.cards).toHaveLength(2);
  });

  it("finds comma when there are no tabs", () => {
    const out = detectFormat("ser,to be\nestar,to be temporarily");
    expect(out.field).toBe("comma");
    expect(out.cards).toHaveLength(2);
  });

  it("finds a semicolon-separated single line", () => {
    const out = detectFormat("a,1; b,2; c,3");
    expect(out.row).toBe("semicolon");
    expect(out.cards).toHaveLength(3);
  });

  it("returns no cards for empty or unparseable input rather than throwing", () => {
    expect(detectFormat("").cards).toEqual([]);
    expect(detectFormat("   \n  \n").cards).toEqual([]);
    expect(detectFormat("one column only\nanother line").cards).toEqual([]);
  });

  it("is deterministic across repeated calls", () => {
    // Ties must not depend on object iteration order.
    const first = detectFormat("a\tb");
    for (let run = 0; run < 20; run += 1) {
      expect(detectFormat("a\tb")).toEqual(first);
    }
  });
});
