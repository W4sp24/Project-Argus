import { describe, expect, it } from "vitest";
import { parseDelimited } from "./parsing";

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
