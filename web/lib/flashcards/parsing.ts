/**
 * The browser twin of `backend/features/flashcards/parsing.py::parse_delimited`.
 *
 * It exists so the import dialog can show what *will* be created before
 * anything is sent. That only helps if the two agree exactly, so the rules
 * here are the same rules, and the unit tests mirror `test_parsing.py`
 * case for case.
 */

export const FIELD_DELIMITERS: Record<string, string> = {
  tab: "\t",
  comma: ",",
  dash: "-",
};

export const ROW_DELIMITERS: Record<string, string> = {
  newline: "\n",
  semicolon: ";",
};

export interface ParsedCard {
  front: string;
  back: string;
}

export type ImportFormat = "qa" | "delimited";

export interface Detected {
  format: ImportFormat;
  field: string;
  row: string;
  cards: ParsedCard[];
}

/**
 * Parse `Q:: … / A:: …` pairs, the shape every note Argus generates carries in
 * its self-test section.
 *
 * A browser twin of `backend/features/flashcards/parsing.py::parse_qa_pairs`,
 * kept only so a dropped file can be *previewed* before it is sent; the server
 * re-parses with the Python original, which stays authoritative. Continuation
 * lines are folded the same way: a field runs until the next `Q::` marker.
 */
export function parseQaPairs(text: string): ParsedCard[] {
  const cards: ParsedCard[] = [];
  let front: string[] | null = null;
  let back: string[] | null = null;
  let mode: "q" | "a" | null = null;

  const flush = () => {
    if (front === null || back === null) return;
    const f = front.join("\n").trim();
    const b = back.join("\n").trim();
    if (f && b) cards.push({ front: f, back: b });
  };

  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("Q::")) {
      flush();
      front = [line.slice(3).trim()];
      back = null;
      mode = "q";
    } else if (line.startsWith("A::")) {
      back = [line.slice(3).trim()];
      mode = "a";
    } else if (mode === "q" && front !== null) {
      front.push(line);
    } else if (mode === "a" && back !== null) {
      back.push(line);
    }
  }
  flush();
  return cards;
}

/**
 * Guess how a pasted or dropped body is laid out.
 *
 * `Q::` wins outright wherever it appears — a note carrying the self-test tail
 * is unambiguous, and a file that has both markers *and* tabs is far more
 * likely to be prose with an indented line than a two-column table.
 *
 * Otherwise every field/row delimiter pairing is tried and the one yielding
 * the most cards wins, with ties broken by the order in `FIELD_DELIMITERS` so
 * the result is deterministic rather than dependent on object iteration.
 *
 * The guess is only ever a starting point: the caller shows it and lets it be
 * overridden. A detector that cannot be corrected is worse than no detector,
 * because a wrong guess is then indistinguishable from a broken file.
 */
export function detectFormat(text: string): Detected {
  const qa = parseQaPairs(text);
  if (qa.length > 0) return { format: "qa", field: "tab", row: "newline", cards: qa };

  let best: Detected = { format: "delimited", field: "tab", row: "newline", cards: [] };
  for (const field of Object.keys(FIELD_DELIMITERS)) {
    for (const row of Object.keys(ROW_DELIMITERS)) {
      const cards = parseDelimited(text, field, row);
      if (cards.length > best.cards.length) {
        best = { format: "delimited", field, row, cards };
      }
    }
  }
  return best;
}

/**
 * Parse pasted rows into cards.
 *
 * Splits on the **first** field delimiter only: a definition legitimately
 * contains commas and hyphens, and splitting greedily would truncate exactly
 * the cards worth writing. Rows with no delimiter, or with either half empty,
 * are dropped rather than imported half-formed — so the count this returns is
 * the count the user will get.
 *
 * An unknown delimiter name yields no cards rather than throwing; the dialog
 * only ever passes names it got from the server.
 */
export function parseDelimited(text: string, field: string, row: string): ParsedCard[] {
  const fieldSep = FIELD_DELIMITERS[field];
  const rowSep = ROW_DELIMITERS[row];
  if (fieldSep === undefined || rowSep === undefined) return [];

  const cards: ParsedCard[] = [];
  for (const line of text.replace(/\r\n/g, "\n").split(rowSep)) {
    const at = line.indexOf(fieldSep);
    if (at === -1) continue;
    const front = line.slice(0, at).trim();
    const back = line.slice(at + fieldSep.length).trim();
    if (front && back) cards.push({ front, back });
  }
  return cards;
}
