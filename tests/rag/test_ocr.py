"""Tests for the OCR pass over image-only PDF pages.

Of the 202 PDF pages in the author's vault, 117 extract as blank: the whole of
ICS26011 is four decks of code screenshots, 133 pages that reached the model
as roughly 2,700 characters between them.

No test here runs a real OCR engine. The engine and the vision escalation are
both injected, which is what keeps this file fast and what keeps every other
suite -- and CI, and the keyring-less e2e run -- on a path that needs no model
and no 40MB of native wheels.
"""

from pathlib import Path

import pytest

from backend.rag.extract import extract_blocks
from backend.rag.extractors.ocr import (
    OcrPolicy,
    OcrResult,
    normalise_ocr_text,
    page_needs_ocr,
    reads_as_merged_words,
)

# A one-page PDF whose text layer says something, used to prove OCR is *not*
# reached when the page can simply be read.
TEXT_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
  /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 90 >> stream
BT /F1 12 Tf 72 720 Td (A class bundles data and the behaviour that acts on it) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""


def _policy(**kwargs) -> OcrPolicy:
    """A policy whose engine returns fixed text, so no model is involved."""
    text = kwargs.pop("text", "recovered from the image")
    confidence = kwargs.pop("confidence", 0.95)
    calls = kwargs.pop("calls", None)

    def engine(png: bytes) -> OcrResult:
        if calls is not None:
            calls.append(len(png))
        return OcrResult(text=text, confidence=confidence)

    return OcrPolicy(engine=engine, **kwargs)


def test_a_page_with_a_text_layer_does_not_need_ocr() -> None:
    assert not page_needs_ocr("A class bundles data and the behaviour" * 2, min_chars=40)


def test_a_blank_page_needs_ocr() -> None:
    assert page_needs_ocr("", min_chars=40)
    assert page_needs_ocr("  3  ", min_chars=40), "a page number is not a text layer"


def test_full_width_punctuation_is_normalised() -> None:
    """OCR reads a code screenshot's brackets as their CJK twins."""
    assert normalise_ocr_text("print（'x'）") == "print('x')"


#: Verbatim RapidOCR output for page 5 of ICS26011's "Dart Classes and OOP",
#: read at mean confidence 0.99. Every glyph is right and every space is
#: gone, which is exactly why confidence cannot be the signal.
MERGED_PAGE = (
    "Whatareclasses?\n"
    "Real-worldentitiesbecome\n"
    "objects\n"
    "Attributesrepresentdata\n"
    "Methodsimplementbehavior"
)

#: Page 11 of the same deck, which read cleanly at 0.93.
CLEAN_PAGE = (
    "class Person\n"
    "// Public properties\n"
    "String name;\n"
    "int age;\n"
    "Person(this.name, this.age, this._ssn);\n"
    "void displayInfo()"
)


def test_merged_words_are_detected_even_at_high_confidence() -> None:
    """The failure that confidence cannot see."""
    assert reads_as_merged_words(MERGED_PAGE)


def test_a_cleanly_read_code_page_is_not_flagged() -> None:
    """Escalating a page that read fine would spend tokens for nothing."""
    assert not reads_as_merged_words(CLEAN_PAGE)
    assert not reads_as_merged_words(""), "nothing to judge is not a failure"


def test_a_long_identifier_does_not_look_like_a_merge() -> None:
    """Real code carries tokens far longer than any English word."""
    assert not reads_as_merged_words(
        "raise NotImplementedError from backend.rag.extractors.ocr import OcrPolicy"
    )


def test_ocr_runs_when_the_page_has_no_text_layer(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    calls: list[int] = []
    blocks = extract_blocks(pdf, ocr=_policy(calls=calls))

    assert len(calls) == 1, "the one blank page was rendered and read"
    assert len(blocks) == 1
    assert blocks[0].text == "recovered from the image"
    assert blocks[0].meta["extraction"] == {"method": "ocr", "confidence": 0.95}


def test_ocr_is_skipped_when_the_page_reads_fine(tmp_path: Path) -> None:
    """OCR at 200dpi costs seconds a page; a readable page must not pay it."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "text.pdf"
    pdf.write_bytes(TEXT_PDF)
    calls: list[int] = []
    blocks = extract_blocks(pdf, ocr=_policy(calls=calls))

    assert calls == [], "a page with a text layer was sent to OCR anyway"
    assert blocks[0].meta["extraction"]["method"] == "text"


def test_no_policy_means_todays_behaviour(tmp_path: Path) -> None:
    """Every existing caller passes nothing and must keep the old path."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    assert extract_blocks(pdf) == []


def test_vision_escalates_a_low_confidence_page(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    seen: list[str] = []

    def describe(png: bytes, prompt: str) -> str:
        seen.append(prompt)
        return "read carefully by a model"

    policy = _policy(confidence=0.3, describe=describe, max_vision_pages=4)
    blocks = extract_blocks(pdf, ocr=policy)

    assert len(seen) == 1
    assert blocks[0].text == "read carefully by a model"
    assert blocks[0].meta["extraction"]["method"] == "vision"


def test_vision_is_off_unless_a_budget_is_given(tmp_path: Path) -> None:
    """A fresh install must never silently spend tokens on a 400-page scan."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    seen: list[str] = []
    policy = _policy(
        confidence=0.1,
        describe=lambda png, prompt: seen.append(prompt) or "never",
    )
    blocks = extract_blocks(pdf, ocr=policy)

    assert seen == [], "max_vision_pages defaults to 0"
    assert blocks[0].meta["extraction"]["method"] == "ocr"


def test_the_vision_budget_is_per_document(tmp_path: Path) -> None:
    """One unreadable deck must not exhaust its budget and then the next."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf(pages=3))
    seen: list[str] = []
    policy = _policy(
        confidence=0.2,
        describe=lambda png, prompt: seen.append(prompt) or "model read it",
        max_vision_pages=2,
    )
    blocks = extract_blocks(pdf, ocr=policy)

    assert len(seen) == 2, "the third page falls back to the OCR text"
    methods = [block.meta["extraction"]["method"] for block in blocks]
    assert methods == ["vision", "vision", "ocr"]


def test_a_failing_vision_call_keeps_the_ocr_text(tmp_path: Path) -> None:
    """A provider outage degrades the page, it does not fail the ingest."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())

    def boom(png: bytes, prompt: str) -> str:
        raise RuntimeError("502 from the provider")

    policy = _policy(confidence=0.2, describe=boom, max_vision_pages=2)
    blocks = extract_blocks(pdf, ocr=policy)

    assert blocks[0].text == "recovered from the image"
    assert blocks[0].meta["extraction"]["method"] == "ocr"


def test_an_empty_ocr_result_produces_no_block(tmp_path: Path) -> None:
    """A genuinely blank page is silence, not an empty string in the corpus."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    assert extract_blocks(pdf, ocr=_policy(text="   ")) == []


def _blank_pdf(pages: int = 1) -> bytes:
    """A PDF with real pages and no text at all -- a scan, as far as we know."""
    pypdfium2 = pytest.importorskip("pypdfium2")
    document = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        document.new_page(200, 200)
    buffer = __import__("io").BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_a_remembered_page_is_not_read_again(tmp_path: Path) -> None:
    """OCR costs ~7s a page; a reindex must not re-pay it for unchanged text."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    store: dict[tuple[str, int], tuple[str, dict]] = {}
    calls: list[int] = []

    def policy() -> OcrPolicy:
        return _policy(
            calls=calls,
            cache_get=lambda key, page: store.get((key, page)),
            cache_put=lambda key, page, text, meta: store.__setitem__((key, page), (text, meta)),
        )

    first = extract_blocks(pdf, ocr=policy())
    assert len(calls) == 1 and len(store) == 1

    second = extract_blocks(pdf, ocr=policy())
    assert len(calls) == 1, "the second pass re-read a page it had already read"
    assert second[0].text == first[0].text
    assert second[0].meta["extraction"] == first[0].meta["extraction"]


def test_editing_the_file_invalidates_its_remembered_pages(tmp_path: Path) -> None:
    """The cache keys on content, so nothing has to notice an edit."""
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(_blank_pdf())
    store: dict[tuple[str, int], tuple[str, dict]] = {}
    calls: list[int] = []
    kwargs = {
        "calls": calls,
        "cache_get": lambda key, page: store.get((key, page)),
        "cache_put": lambda key, page, text, meta: store.__setitem__((key, page), (text, meta)),
    }
    extract_blocks(pdf, ocr=_policy(**kwargs))
    pdf.write_bytes(_blank_pdf(pages=2))
    extract_blocks(pdf, ocr=_policy(**kwargs))

    assert len(calls) == 3, "one page first time, two after the edit"
