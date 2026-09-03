"""Turning text into cards, by the two routes that do not involve a model.

``parse_qa_pairs`` reads the ``Q::``/``A::`` convention the vault already uses
— every note ingest writes carries a self-test tail in exactly this shape
(``backend/agent/prompts/note_quality.md``), which is what makes "import from
a note" work against any note rather than only against ``flashcards.md``.

``parse_delimited`` reads pasted rows, the affordance every flashcard tool
offers: choose what separates the two halves and what separates the rows,
paste, preview. ``web/lib/flashcards/parsing.ts`` is its twin, so the preview
shown before importing is the same parse the server will perform.
"""

from __future__ import annotations

#: What may separate a card's two halves. Keys are the wire values.
FIELD_DELIMITERS: dict[str, str] = {"tab": "\t", "comma": ",", "dash": "-"}

#: What may separate one card from the next.
ROW_DELIMITERS: dict[str, str] = {"newline": "\n", "semicolon": ";"}


def parse_qa_pairs(text: str) -> list[tuple[str, str]]:
    """Parse ``Q:: <front>`` / ``A:: <back>`` pairs from markdown.

    Each field may continue on following lines up to the next ``Q::`` marker
    (or end of text). Pairs missing either half are dropped.
    """
    pairs: list[tuple[str, str]] = []
    front_lines: list[str] | None = None
    back_lines: list[str] | None = None
    mode: str | None = None

    def flush() -> None:
        if front_lines is not None and back_lines is not None:
            front = "\n".join(front_lines).strip()
            back = "\n".join(back_lines).strip()
            if front and back:
                pairs.append((front, back))

    for line in text.splitlines():
        if line.startswith("Q::"):
            flush()
            front_lines = [line[3:].strip()]
            back_lines = None
            mode = "q"
        elif line.startswith("A::"):
            back_lines = [line[3:].strip()]
            mode = "a"
        elif mode == "q" and front_lines is not None:
            front_lines.append(line)
        elif mode == "a" and back_lines is not None:
            back_lines.append(line)
    flush()
    return pairs


def parse_delimited(text: str, *, field: str, row: str) -> list[tuple[str, str]]:
    """Parse pasted rows into ``(front, back)`` pairs.

    Splits on the **first** field delimiter only. A definition legitimately
    contains commas and hyphens, and splitting greedily would truncate exactly
    the cards worth writing.

    Rows lacking a delimiter, or with either half empty, are dropped rather
    than imported half-formed — a card with no answer is not a card, and the
    preview counts what will actually be created.

    Raises ``KeyError`` for an unknown delimiter name; these arrive from a
    request body and are not trusted input.
    """
    field_sep = FIELD_DELIMITERS[field]
    row_sep = ROW_DELIMITERS[row]
    pairs: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").split(row_sep):
        head, sep, tail = line.partition(field_sep)
        if not sep:
            continue
        front, back = head.strip(), tail.strip()
        if front and back:
            pairs.append((front, back))
    return pairs
