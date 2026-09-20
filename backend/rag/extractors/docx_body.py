"""Read a Word document as sections, keeping its tables.

The previous extractor returned every paragraph joined into a single block,
which cost two things. Tables vanished entirely -- a ``docx`` table is not a
paragraph, so a document whose content was a comparison grid indexed as its
prose only. And one block for a whole document means one citation for a whole
document: a chunk from page 30 is indistinguishable from one from page 1, so
a note generated from it can say where a claim came from only as far as the
filename.

Splitting on headings is the best available proxy for "where in the document"
in a format that has no page numbers until it is laid out.
"""

from __future__ import annotations

import re
from typing import Any

#: A Word heading style is "Heading 1".."Heading 9", and localised builds
#: append the level to a translated word, so match on the trailing digit
#: rather than on the English name.
_HEADING_STYLE = re.compile(r"heading\s*([1-9])", re.IGNORECASE)

#: Title and Subtitle open a document rather than divide it, so they start a
#: section too -- otherwise everything before the first real heading is
#: section-less and gets attributed to nothing.
_OPENING_STYLES = frozenset({"title", "subtitle"})


def _heading_level(paragraph: Any) -> int | None:
    """The outline level of a paragraph, or ``None`` if it is body text."""
    name = getattr(getattr(paragraph, "style", None), "name", "") or ""
    if name.strip().lower() in _OPENING_STYLES:
        return 1
    match = _HEADING_STYLE.search(name)
    return int(match.group(1)) if match else None


def _table_markdown(table: Any) -> list[str]:
    """One ``docx`` table as GitHub-flavoured Markdown rows."""
    rows: list[str] = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) or " " for cell in row.cells]
        if not cells:
            continue
        rows.append("| " + " | ".join(cells) + " |")
        if len(rows) == 1:
            rows.append("| " + " | ".join("---" for _ in cells) + " |")
    return rows


def _body_items(document: Any):
    """Paragraphs and tables of the body, in the order they appear.

    ``document.paragraphs`` and ``document.tables`` are two separate lists
    with no interleaving, so reading them in turn would move every table to
    the end of whatever section happened to be last. The body's own XML is the
    only place that ordering survives.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        tag = str(child.tag).rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def document_sections(document: Any) -> list[tuple[str, str]]:
    """``(heading, text)`` for each section of the document.

    The heading is ``""`` for anything before the first one. A document with
    no headings at all comes back as one section, which is exactly the old
    behaviour and the right answer for a document that genuinely has no
    structure.
    """
    from docx.table import Table

    sections: list[tuple[str, list[str]]] = [("", [])]
    for item in _body_items(document):
        if isinstance(item, Table):
            sections[-1][1].extend(_table_markdown(item))
            continue
        text = " ".join(item.text.split())
        if not text:
            continue
        if _heading_level(item) is not None:
            sections.append((text, [text]))
        else:
            sections[-1][1].append(text)
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if "\n".join(lines).strip()
    ]
