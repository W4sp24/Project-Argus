"""Read PDF pages that have no text layer.

A slide deck exported to PDF is a stack of pictures. ``pdfplumber`` asks such
a page for its text and is told, correctly, that there is none -- so the file
indexes to almost nothing and every note, exam and flashcard generated from it
is written about a document nobody read. Of the 202 PDF pages in the author's
vault, 117 are in that state, including all four ICS26011 decks.

Two passes, in cost order:

1. **Local OCR**, offline and free, via ``rapidocr-onnxruntime``. Good on the
   code screenshots and diagrams that make up most such pages.
2. **A vision model**, when local OCR clearly struggled *and* the caller has
   given a budget. Off unless asked for.

The model is never imported here. ``rag/`` does not import ``agent/`` -- the
dependency runs the other way, and inverting it would make ``extract.py``
unimportable without the whole agent stack. Instead a caller that wants
escalation passes a ``describe`` callable in its :class:`OcrPolicy`, which
:mod:`backend.main` builds. It is deliberately synchronous: ``extract_blocks``
is reached from ``VaultIndex.upsert_file`` inside ``reindex_all``'s loop, on a
worker thread, and making it async would turn all of ``rag/index.py`` async
for one optional feature.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("argus.rag")

#: What a page is asked for when a vision model reads it. It says "transcribe"
#: rather than "describe" on purpose: this text is indexed and cited, so a
#: model narrating what it sees would put words in the source's mouth.
VISION_PROMPT = (
    "Transcribe this slide exactly. Output only what is written on it, as "
    "Markdown. Keep code in fenced blocks with its indentation, keep tables as "
    "Markdown tables, and write any mathematics as LaTeX between $ delimiters. "
    "Do not summarise, explain, or add anything that is not on the slide. If "
    "the slide is a picture with no text, reply with nothing at all."
)

#: Below this many characters, a page is treated as having no text layer. A
#: slide exported to PDF often still carries its page number, so the threshold
#: has to sit above "a digit and some whitespace".
MIN_NATIVE_CHARS = 40

#: Render resolution. 200 reads a code screenshot cleanly and is roughly twice
#: as fast as 300, which matters when a deck is 55 pages.
DEFAULT_DPI = 200

#: Local OCR below this is not worth citing without a second opinion.
LOW_CONFIDENCE = 0.6

#: Full-width and typographic characters OCR substitutes for ASCII. A code
#: screenshot read back with a CJK parenthesis in it is not runnable code and
#: does not match a search for the function that contains it.
_LOOKALIKES = {
    "（": "(", "）": ")", "［": "[", "］": "]",
    "｛": "{", "｝": "}", "：": ":", "；": ";",
    "，": ",", "．": ".", "＝": "=", "＋": "+",
    "＜": "<", "＞": ">", "！": "!", "？": "?",
    "％": "%", "＄": "$", "＃": "#", "＆": "&",
    "＊": "*", "／": "/", "＼": "\\", "｜": "|",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
}

#: An unbroken run of letters this long is a word-merge, not a word. Set
#: above the longest identifiers that legitimately appear in the material
#: ("NotImplementedError" is 19) so real code is never flagged.
_MERGED_RUN_CHARS = 20

#: English averages about five characters a word, and even dense technical
#: prose stays near seven. A page averaging more than this has had its spaces
#: eaten -- which is the whole failure, since each individual glyph was read
#: correctly and confidently.
_MERGED_MEAN_CHARS = 11

#: Below this there is not enough on the page to average anything.
_MERGED_MIN_TOKENS = 4

#: Punctuation that makes a long token legitimate: a path, a URL, a dotted
#: identifier or a call. Such tokens are excluded from the average rather
#: than counted as evidence of merging.
_STRUCTURED = re.compile(r"[_./(){}\[\]:;=<>#@\\]|https?")


@dataclass(frozen=True)
class OcrResult:
    """What one OCR pass made of one page image."""

    text: str
    confidence: float


#: Reads a PNG and returns what it says.
OcrEngine = Callable[[bytes], OcrResult]

#: Reads a PNG against a prompt and returns text. Supplied by the caller so
#: that ``rag/`` never imports ``agent/``.
Describe = Callable[[bytes, str], str]


#: Looks up a remembered page, or stores one. Injected for the same reason
#: ``describe`` is: ``rag/`` has no database connection and should not grow
#: one for a cache.
CacheGet = Callable[[str, int], "tuple[str, dict] | None"]
CachePut = Callable[[str, int, str, dict], None]


@dataclass(frozen=True)
class OcrPolicy:
    """Whether, and how hard, to try to read an image-only page.

    The default is the honest one for a fresh install: local OCR if the engine
    is installed, and no model call at all. ``max_vision_pages`` stays 0 until
    somebody asks for it, so nobody's first ingest quietly spends tokens
    transcribing a 400-page scan.
    """

    dpi: int = DEFAULT_DPI
    min_native_chars: int = MIN_NATIVE_CHARS
    engine: OcrEngine | None = None
    describe: Describe | None = None
    max_vision_pages: int = 0
    cache_get: CacheGet | None = None
    cache_put: CachePut | None = None


def page_needs_ocr(native_text: str, *, min_chars: int = MIN_NATIVE_CHARS) -> bool:
    """Whether a page's own text layer is too thin to be the page."""
    return len(native_text.strip()) < min_chars


def normalise_ocr_text(text: str) -> str:
    """Fold the characters OCR substitutes for ASCII punctuation."""
    for source, target in _LOOKALIKES.items():
        text = text.replace(source, target)
    return text


def reads_as_merged_words(text: str) -> bool:
    """Whether OCR ran words together -- the failure confidence cannot see.

    A large heading set in a wide font comes back as one token,
    ``Whatareclasses?``, and the engine reports 0.99 doing it because it is
    entirely certain of every glyph it read. Confidence is therefore useless
    here, and only the shape of the result gives it away: a run of letters far
    longer than any identifier in the material, carrying none of the
    punctuation that makes a long identifier legitimate.
    """
    words = [token for token in text.split() if not _STRUCTURED.search(token)]
    if not words:
        return False
    # One very long run of letters is conclusive on its own: no identifier in
    # the material is this long, and a hyphen does not make it a real word
    # ("Real-worldentitiesbecome" is two merges joined by one).
    for token in words:
        if any(len(run) >= _MERGED_RUN_CHARS for run in re.findall(r"[A-Za-z]+", token)):
            return True
    if len(words) < _MERGED_MIN_TOKENS:
        return False
    return sum(len(word) for word in words) / len(words) > _MERGED_MEAN_CHARS


def default_engine() -> OcrEngine | None:
    """The bundled local OCR engine, or ``None`` when it is not installed.

    Absence is a logged degradation rather than an error: the ``[rag]`` extra
    is optional, the base install has never had it, and a packaged build that
    froze the wheels wrongly should lose OCR rather than fail to start.
    """
    try:
        import numpy
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # noqa: BLE001 - any import failure means "absent"
        logger.info("local OCR unavailable (%s); image-only pages stay unread", exc)
        return None

    reader = RapidOCR()

    def run(png: bytes) -> OcrResult:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(png)).convert("RGB")
        result, _elapsed = reader(numpy.array(image))
        if not result:
            return OcrResult(text="", confidence=0.0)
        lines = [str(entry[1]) for entry in result]
        scores = [float(entry[2]) for entry in result]
        return OcrResult(
            text="\n".join(lines),
            confidence=sum(scores) / len(scores) if scores else 0.0,
        )

    return run


def file_digest(path: Path) -> str:
    """A content hash for one file, used as the extraction cache's key.

    Re-exported from :mod:`backend.core.extraction_cache` so that callers in
    ``rag/`` have one import for the whole OCR path; ``core/`` is platform, so
    depending on it does not bend the layering the way importing a feature
    would.
    """
    from backend.core.extraction_cache import file_hash

    return file_hash(path)


def render_page(path: Path, index: int, *, dpi: int = DEFAULT_DPI) -> bytes:
    """Render one zero-based page of a PDF to PNG bytes."""
    from io import BytesIO

    import pypdfium2

    document = pypdfium2.PdfDocument(path)
    try:
        image = document[index].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        document.close()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def read_page_image(
    png: bytes, policy: OcrPolicy, *, vision_budget: list[int]
) -> tuple[str, dict]:
    """Read one rendered page, escalating to a model only if allowed.

    ``vision_budget`` is a one-element list used as a mutable counter so the
    cap is per document rather than per page -- one unreadable deck must not
    exhaust its allowance and then hand the next deck a fresh one.

    Returns the text and the metadata describing how it was obtained, so a
    reader of the resulting note can tell a transcription from a reading.
    """
    engine = policy.engine or default_engine()
    if engine is None:
        return "", {"method": "none"}

    result = engine(png)
    text = normalise_ocr_text(result.text).strip()
    meta = {"method": "ocr", "confidence": round(result.confidence, 2)}

    struggling = result.confidence < LOW_CONFIDENCE or reads_as_merged_words(text)
    if policy.describe and struggling and vision_budget[0] > 0:
        # Spent before the call, not after: the budget caps *attempts*, so a
        # provider that is down costs a 55-page deck two failed calls rather
        # than fifty-five.
        vision_budget[0] -= 1
        try:
            better = policy.describe(png, VISION_PROMPT)
        except Exception as exc:  # noqa: BLE001 - an outage degrades, never fails
            logger.warning("vision escalation failed, keeping the OCR text: %s", exc)
        else:
            if better and better.strip():
                return normalise_ocr_text(better).strip(), {"method": "vision"}
    return text, meta
