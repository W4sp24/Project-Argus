"""Turn vault files into plain-text blocks with citation metadata.

Non-markdown course materials keep their page/slide numbers so downstream
answers can cite "file p.N" / "slide N" (invariant I6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

from backend.rag.extractors.ocr import OcrPolicy, page_needs_ocr
from backend.vault.privacy import is_no_ai

logger = logging.getLogger("argus.rag")


@dataclass
class Block:
    """One extractable unit of text plus citation metadata."""

    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _extract_markdown(file_path: Path) -> list[Block]:
    # No try/except here: extract_blocks already wraps every extractor call in
    # one, so failures are logged and collected in exactly one place.
    post = frontmatter.load(file_path)
    if is_no_ai(post):
        return []  # I3: tagged notes never enter the pipeline
    if not post.content.strip():
        return []
    return [Block(text=post.content, meta={"frontmatter": dict(post.metadata)})]


def _extract_pdf(file_path: Path, ocr: OcrPolicy | None = None) -> list[Block]:
    """One block per page, falling back to OCR where there is no text layer.

    A slide deck exported to PDF is a stack of pictures, and asking such a
    page for its text correctly returns nothing. Without ``ocr`` that is still
    where this stops -- every existing caller passes nothing and keeps the old
    behaviour.
    """
    import pdfplumber  # heavy import kept lazy

    blocks: list[Block] = []
    # A one-element list so read_page_image can spend from it: the vision cap
    # is per document, not per page.
    budget = [ocr.max_vision_pages if ocr else 0]
    # Computed at most once per file, and only when something actually needs
    # reading: hashing every PDF on every reindex would be a cost of its own.
    digest: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            meta: dict[str, Any] = {"page": number, "extraction": {"method": "text"}}
            if ocr is not None and page_needs_ocr(text, min_chars=ocr.min_native_chars):
                text, method = _read_page_image(file_path, number, ocr, budget, digest)
                meta["extraction"] = method
            if text:
                blocks.append(Block(text=text, meta=meta))
    return blocks


def _read_page_image(
    file_path: Path, page: int, ocr: OcrPolicy, budget: list[int], digest: list[str]
) -> tuple[str, dict[str, Any]]:
    """Render and read one 1-based page, degrading to silence if it fails."""
    from backend.rag.extractors.ocr import file_digest, read_page_image, render_page

    if (ocr.cache_get or ocr.cache_put) and not digest:
        digest.append(file_digest(file_path))
    key = digest[0] if digest else ""

    if ocr.cache_get and key:
        remembered = ocr.cache_get(key, page)
        if remembered is not None:
            return remembered

    try:
        png = render_page(file_path, page - 1, dpi=ocr.dpi)
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the file
        logger.warning("failed to render %s page %s: %s", file_path, page, exc)
        return "", {"method": "none"}

    text, method = read_page_image(png, ocr, vision_budget=budget)
    # A page that read as nothing is still worth remembering: it is usually a
    # genuinely blank or purely pictorial page, and re-deriving that costs the
    # same seven seconds as re-deriving real text.
    if ocr.cache_put and key and method.get("method") != "none":
        ocr.cache_put(key, page, text, method)
    return text, method


def _extract_pptx(file_path: Path) -> list[Block]:
    """One block per slide, with equations, groups, tables and notes.

    ``slide.shapes`` is not used: it is a shallow iterator over recognised
    shape elements and it skips ``mc:AlternateContent`` (where every equation
    lives), group contents, and tables. See
    :mod:`backend.rag.extractors.pptx_shapes`.
    """
    from pptx import Presentation  # heavy import kept lazy

    from backend.rag.extractors.pptx_shapes import slide_lines, speaker_notes

    blocks: list[Block] = []
    for number, slide in enumerate(Presentation(file_path).slides, start=1):
        lines = slide_lines(slide)
        notes = speaker_notes(slide)
        if notes:
            # Marked rather than merged: a note is the lecturer talking, not
            # something the slide claims, and a reader revising from the block
            # needs to be able to tell those apart.
            lines.append(f"[notes] {notes}")
        if lines:
            blocks.append(
                Block(
                    text="\n".join(lines),
                    meta={"slide": number, "extraction": {"method": "text"}},
                )
            )
    return blocks


def _extract_docx(file_path: Path) -> list[Block]:
    import docx  # heavy import kept lazy

    paragraphs = [p.text for p in docx.Document(file_path).paragraphs if p.text.strip()]
    if not paragraphs:
        return []
    return [Block(text="\n".join(paragraphs), meta={})]


def _extract_eml(file_path: Path) -> list[Block]:
    """One block per email: headers worth searching, then the plain body.

    The ingest route has accepted ``.eml`` since it was written, but there was
    no extractor for it -- so a dropped email was saved to the vault and then
    indexed to zero chunks, surfacing as "saved -- indexing unavailable". The
    subject and sender are prepended to the block text because searching for
    who a mail was from is at least as common as searching its body.
    """
    from backend.rag.email import parse_email

    parsed = parse_email(file_path.read_text(encoding="utf-8", errors="replace"))
    body = str(parsed.get("body") or "").strip()
    header_lines = [
        f"{label}: {parsed[key]}"
        for label, key in (("Subject", "subject"), ("From", "sender"), ("Date", "date"))
        if parsed.get(key)
    ]
    text = "\n".join([*header_lines, "", body]).strip() if header_lines else body
    if not text:
        return []
    meta = {key: parsed[key] for key in ("subject", "sender", "date") if parsed.get(key)}
    return [Block(text=text, meta=meta)]


_EXTRACTORS = {
    ".md": _extract_markdown,
    ".pdf": _extract_pdf,
    ".pptx": _extract_pptx,
    ".docx": _extract_docx,
    ".eml": _extract_eml,
}


def extract_blocks(
    file_path: Path,
    *,
    errors: list[str] | None = None,
    ocr: OcrPolicy | None = None,
) -> list[Block]:
    """Extract text blocks from a supported file; unsupported types yield [].

    ``errors``, when given, receives one message on failure. The empty-list
    return is unchanged either way — a single unreadable file must never break
    a caller that extracts many (e.g. a full reindex) — but a caller that
    wants to know *why* a file came back empty (rather than assume it was
    legitimately blank) can pass a list and inspect it afterward.

    ``ocr`` turns on reading of image-only PDF pages. It is injected rather
    than constructed here so that ``rag/`` keeps out of ``agent/``: see
    :mod:`backend.rag.extractors.ocr`. Omitting it — which every caller did
    before it existed — leaves the behaviour exactly as it was.
    """
    extractor = _EXTRACTORS.get(file_path.suffix.lower())
    if extractor is None:
        return []
    try:
        if extractor is _extract_pdf:
            return _extract_pdf(file_path, ocr)
        return extractor(file_path)
    except Exception as exc:
        logger.warning("failed to extract %s: %s", file_path, exc)
        if errors is not None:
            errors.append(str(exc))
        return []
