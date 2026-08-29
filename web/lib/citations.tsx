/**
 * Citation helpers for the chat surfaces.
 *
 * This file used to export `renderWithCitations`, which regex-scraped
 * `[path.md]` tokens out of the answer *text* and returned React nodes. Its
 * own docstring explained why: "the `/ws/chat` frame protocol carries no
 * structured citations field". That is no longer true. The `tool` end frame
 * carries a `paths` list which `backend/features/chat/router.py` has already
 * filtered through `is_indexable`, so the chips are now built from the trace
 * instead of inferred from prose — which means invariant I3 (nothing from
 * `99-Private/` or a `#no-ai` note ever reaches the browser) holds by
 * construction rather than by whatever the model happened to type.
 *
 * Two things survive the change:
 *
 * `stripCitationMarkers` removes the inline `[path.md]` markers that
 * `prompts/chat.md` still asks the model to emit, so they do not double up
 * with the chip row underneath. It must run *before* markdown rendering: a
 * helper that returns React nodes cannot compose with react-markdown, which
 * takes a string and owns the whole tree.
 *
 * `obsidianUri` builds the deep link the chips point at. It addresses the note
 * by absolute path rather than by vault name: `vault=` is matched against the
 * vault's *registered* name in Obsidian, which is set when the vault is added
 * and is independent of what the folder is called afterwards. A mismatch there
 * is Obsidian's own "Vault not found" dialog, reported against a vault called
 * Second Brain, and it was unfixable from this side because nothing here knows
 * the registered name. `path=` cannot mismatch.
 */

/**
 * A citation marker — `[path.md]`, `[deck.pdf p.4]`, `[slides.pptx slide 9]` —
 * together with the horizontal whitespace on either side of it.
 *
 * The seam is part of the pattern rather than tidied afterwards, and that is
 * the whole point. This used to remove the markers and then run
 * `/[ \t]{2,}/g -> " "` over the entire answer to clean up after itself, which
 * cannot tell the double space it just created from indentation the model
 * meant: a nested list item written as `"  - sub-point"` came out as
 * `" - sub-point"` and rendered flat, and an indented code block lost its
 * indent, in every answer, whether or not it contained a citation at all.
 *
 * Anchoring to the marker means untouched text stays byte-identical.
 */
const CITATION_SEAM =
  /[ \t]*\[[^[\]\n]+?\.(?:md|pdf|pptx|docx)(?:\s+(?:p\.|slide\s)?\d+)?\][ \t]*/g;

/** Punctuation that must close up against the word before it. */
const CLINGS_LEFT = ".,;:!?)]}";

export function stripCitationMarkers(text: string): string {
  return text.replace(CITATION_SEAM, (seam, offset: number, whole: string) => {
    const before = whole[offset - 1] ?? "";
    const after = whole[offset + seam.length] ?? "";
    // "the notes [x.md]." -> "the notes."; likewise at a line or text end.
    if (after === "" || after === "\n" || CLINGS_LEFT.includes(after)) return "";
    // "[x.md] The notes" and "(see [x.md] here)" -> no leading space either.
    if (before === "" || before === "\n" || before === "(") return "";
    // "the notes [x.md] say" -> "the notes say".
    return " ";
  });
}

export function obsidianUri(vaultPath: string, path: string): string {
  const absolute = `${vaultPath.replace(/\/+$/, "")}/${path}`;
  return `obsidian://open?path=${encodeURIComponent(absolute)}`;
}
