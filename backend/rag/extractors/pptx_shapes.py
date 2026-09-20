"""Read every piece of text on a PowerPoint slide, in document order.

``python-pptx``'s ``slide.shapes`` is a shallow iterator over the shape
elements it recognises, and four kinds of content are invisible to it:

* anything inside ``mc:AlternateContent`` -- which is where PowerPoint puts
  equations, so *every* equation in a deck was being dropped;
* anything inside a group;
* tables, which are ``GraphicFrame``s with no text frame at all;
* speaker notes, which often carry the explanation the slide only gestures at.

So this module walks the shape tree's XML directly rather than the object
model. It returns plain strings and leaves ``Block`` construction to
:mod:`backend.rag.extract`, which keeps the dependency pointing one way.
"""

from __future__ import annotations

from typing import Any

from backend.rag.extractors.omml import M_NS, omml_to_latex

#: DrawingML -- paragraphs, runs and the text inside them.
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
#: Markup Compatibility -- the Choice/Fallback wrapper around equations.
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

#: Notes placeholders that are furniture rather than content: the slide
#: thumbnail and the page number are on every notes slide whether or not
#: anybody typed a note.
_NOTES_FURNITURE = frozenset({"sldImg", "sldNum", "dt", "ftr", "hdr"})


def _local(element: Any) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _uri(element: Any) -> str:
    tag = str(element.tag)
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _preferred(alternate: Any) -> Any | None:
    """The branch of an ``mc:AlternateContent`` worth reading.

    ``mc:Choice`` is the real content -- the shape with the live equation in
    it. ``mc:Fallback`` is normally a picture of that same content for readers
    that cannot render it, so taking both would duplicate the slide. Some
    writers emit only a Fallback, and half a slide beats none of it.
    """
    fallback = None
    for child in alternate:
        name = _local(child)
        if name == "Choice":
            return child
        if name == "Fallback" and fallback is None:
            fallback = child
    return fallback


def _paragraph_text(paragraph: Any) -> str:
    """One ``a:p``, with its equations inlined as ``$...$`` where they sit.

    Position matters: an equation appended to the end of a slide's text is a
    loose formula, while one left in place is the predicate of the sentence
    around it.
    """
    pieces: list[str] = []

    def walk(node: Any) -> None:
        name, uri = _local(node), _uri(node)
        if uri == M_NS and name in ("oMath", "oMathPara"):
            latex = omml_to_latex(node)
            if latex:
                pieces.append(f"${latex}$")
            return  # already consumed; its children are math, not prose
        if uri == A_NS and name == "t":
            pieces.append(node.text or "")
            return
        if uri == A_NS and name == "br":
            pieces.append(" ")
            return
        if uri == MC_NS and name == "AlternateContent":
            chosen = _preferred(node)
            if chosen is not None:
                for child in chosen:
                    walk(child)
            return
        for child in node:
            walk(child)

    walk(paragraph)
    return " ".join("".join(pieces).split())


def _cell_text(cell: Any) -> str:
    return " ".join(
        text
        for paragraph in cell.iter(f"{{{A_NS}}}p")
        if (text := _paragraph_text(paragraph))
    )


def _walk_tree(node: Any, lines: list[str]) -> None:
    """Collect one line per paragraph, descending into what pptx skips."""
    name, uri = _local(node), _uri(node)

    if uri == MC_NS and name == "AlternateContent":
        chosen = _preferred(node)
        if chosen is not None:
            for child in chosen:
                _walk_tree(child, lines)
        return
    if uri == A_NS and name == "tbl":
        lines.extend(_markdown_table(node))
        return
    if uri == A_NS and name == "p":
        text = _paragraph_text(node)
        if text:
            lines.append(text)
        return
    for child in node:
        _walk_tree(child, lines)


def _markdown_table(table: Any) -> list[str]:
    """An ``a:tbl`` rendered as a Markdown table."""
    rows: list[str] = []
    for row in table:
        if _local(row) != "tr":
            continue
        cells = [_cell_text(cell).strip() or " " for cell in row if _local(cell) == "tc"]
        if not cells:
            continue
        rows.append("| " + " | ".join(cells) + " |")
        if len(rows) == 1:
            # Emitted unconditionally: a table with no header separator
            # renders as a run-on paragraph in Obsidian and in the app.
            rows.append("| " + " | ".join("---" for _ in cells) + " |")
    return rows


def slide_lines(slide: Any) -> list[str]:
    """Every line of text on one slide, in document order.

    Equations arrive inline as ``$...$``; tables arrive as Markdown rows.
    """
    lines: list[str] = []
    _walk_tree(slide.shapes._spTree, lines)
    return lines


def speaker_notes(slide: Any) -> str:
    """The typed speaker notes, without the notes slide's own furniture.

    A notes slide always carries a thumbnail of the slide and a page-number
    placeholder, so reading all of its text would stamp a bare number onto
    every block whether or not anybody wrote a note.
    """
    if not slide.has_notes_slide:
        return ""
    notes = slide.notes_slide
    lines: list[str] = []
    for shape in notes.shapes:
        if not shape.has_text_frame:
            continue
        if shape.is_placeholder and shape.placeholder_format.type is not None:
            kind = str(shape.placeholder_format.type).split()[0].lower()
            if any(kind.startswith(name.lower()) for name in _NOTES_FURNITURE):
                continue
        collected: list[str] = []
        _walk_tree(shape._element, collected)
        lines.extend(collected)
    return "\n".join(lines).strip()
