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
