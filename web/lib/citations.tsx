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
 * `obsidianUri` builds the deep link the chips point at.
 */

/** Matches `[path.md]`, `[deck.pdf p.4]`, `[slides.pptx slide 9]`. */
const CITATION_MARKER = /\[[^[\]\n]+?\.(?:md|pdf|pptx|docx)(?:\s+(?:p\.|slide\s)?\d+)?\]/g;

export function stripCitationMarkers(text: string): string {
  return (
    text
      .replace(CITATION_MARKER, "")
      // A marker removed mid-sentence leaves "the note  says" or "the note ."
      // behind. Tidying the seam is cosmetic, but the alternative is visible
      // in every grounded answer.
      .replace(/[ \t]{2,}/g, " ")
      .replace(/[ \t]+([.,;:!?)])/g, "$1")
      .replace(/\(\s+/g, "(")
  );
}

export function obsidianUri(vaultName: string, path: string): string {
  return `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(path)}`;
}
