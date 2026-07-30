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

logger = logging.getLogger("argus.rag")

NO_AI_TAG = "no-ai"


@dataclass
class Block:
    """One extractable unit of text plus citation metadata."""

    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _is_private(post: frontmatter.Post) -> bool:
    tags = post.metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if NO_AI_TAG in [str(tag).strip().lstrip("#") for tag in tags]:
        return True
    return f"#{NO_AI_TAG}" in post.content


def _extract_markdown(file_path: Path) -> list[Block]:
    # No try/except here: extract_blocks already wraps every extractor call in
    # one, so failures are logged and collected in exactly one place.
    post = frontmatter.load(file_path)
    if _is_private(post):
        return []  # I3: tagged notes never enter the pipeline
    if not post.content.strip():
        return []
    return [Block(text=post.content, meta={"frontmatter": dict(post.metadata)})]


def _extract_pdf(file_path: Path) -> list[Block]:
    import pdfplumber  # heavy import kept lazy

    blocks: list[Block] = []
    with pdfplumber.open(file_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(Block(text=text, meta={"page": number}))
    return blocks


def _extract_pptx(file_path: Path) -> list[Block]:
    from pptx import Presentation  # heavy import kept lazy

    blocks: list[Block] = []
    for number, slide in enumerate(Presentation(file_path).slides, start=1):
        texts = [
            shape.text_frame.text
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        if texts:
            blocks.append(Block(text="\n".join(texts), meta={"slide": number}))
    return blocks


def _extract_docx(file_path: Path) -> list[Block]:
    import docx  # heavy import kept lazy

    paragraphs = [p.text for p in docx.Document(file_path).paragraphs if p.text.strip()]
    if not paragraphs:
        return []
    return [Block(text="\n".join(paragraphs), meta={})]


_EXTRACTORS = {
    ".md": _extract_markdown,
    ".pdf": _extract_pdf,
    ".pptx": _extract_pptx,
    ".docx": _extract_docx,
}


def extract_blocks(file_path: Path, *, errors: list[str] | None = None) -> list[Block]:
    """Extract text blocks from a supported file; unsupported types yield [].

    ``errors``, when given, receives one message on failure. The empty-list
    return is unchanged either way — a single unreadable file must never break
    a caller that extracts many (e.g. a full reindex) — but a caller that
    wants to know *why* a file came back empty (rather than assume it was
    legitimately blank) can pass a list and inspect it afterward.
    """
    extractor = _EXTRACTORS.get(file_path.suffix.lower())
    if extractor is None:
        return []
    try:
        return extractor(file_path)
    except Exception as exc:
        logger.warning("failed to extract %s: %s", file_path, exc)
        if errors is not None:
            errors.append(str(exc))
        return []
