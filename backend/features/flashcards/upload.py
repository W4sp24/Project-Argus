"""Cards from a file you hand over once, which Argus never keeps.

The deck library's GENERATE could only ever read a *course* -- everything in it,
or the files ticked in a Course Hub's SOURCES rail. A lecture that is not in the
vault, or not indexed yet, had no route in at all: you had to ingest it first,
wait for the embedding model, and only then generate.

This is the other route. The file is streamed to a temp path outside the vault,
its text extracted, the temp path deleted, and the text used for exactly one
prompt. Nothing is written to the vault, so I1 and I2 have nothing to say about
it; nothing is indexed, so the corpus is built here rather than by
``course_corpus``. The dialog offers "also save it into materials" separately,
and that goes through the normal ingest flow, because saving a file *is* a vault
write and must be one.

Three things this module exists to get right, none of them obvious:

* **Extraction needs a real path with the real suffix.** ``extract_blocks``
  dispatches on ``file_path.suffix`` and every extractor opens the path
  (``pdfplumber.open``, ``Presentation``, ``docx.Document``), so an in-memory
  buffer cannot be passed to it. Hence the temp file, and hence
  ``NamedTemporaryFile(suffix=...)`` rather than a bare one.

* **A block can be bigger than the whole prompt budget.** ``.docx`` returns one
  block for the entire document. ``deck_prompt`` packs blocks until
  ``MAX_PROMPT_CHARS`` and ``break``s, testing the *whole* block -- so a single
  oversized block fails that test on the first iteration and the prompt ships
  with an empty ``SOURCES:`` section. The corpus is non-empty, so
  ``generate_cards``' own guard does not fire either, and the model writes
  plausible cards from nothing. Splitting here is the fix; ``rag/chunk.py`` is
  not, because it derives a course from a vault-relative path an upload has not
  got, and its overlapping windows duplicate text inside a fixed budget for a
  retrieval step that never happens.

* **An unsupported suffix must be refused, not extracted.** ``extract_blocks``
  returns ``[]`` for an unknown type *and* appends nothing to ``errors``, which
  is indistinguishable from a blank file -- so ``.txt``/``.csv`` would be
  reported as "no text in your file". Those are read in the browser instead, and
  anything not in :data:`UPLOAD_SUFFIXES` is a 422 before a byte is copied.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import IO, Any

from backend.features.flashcards.store import FlashcardsError
from backend.rag.extract import extract_blocks

#: What a one-shot upload may be. Narrower than the ingest pipeline's set:
#: ``.eml`` is not a thing anyone makes flashcards from, and ``.txt``/``.csv``
#: have no extractor at all -- they would come back empty and be reported as a
#: blank file. A ``.csv`` of card rows is IMPORT's job, not generation's.
UPLOAD_SUFFIXES: tuple[str, ...] = (".pdf", ".pptx", ".docx", ".md")

#: Far below ingest's 100MB. That number is sized for a file being committed
#: into a git-backed vault and kept; this one is read once and deleted, and the
#: prompt it feeds is capped at 60k characters regardless.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

#: Copy in slices rather than ``read()``, so the size limit is enforced against
#: a 900MB body instead of after it is already in memory.
COPY_CHUNK_BYTES = 1024 * 1024

#: One corpus entry's ceiling. Small enough that many survive
#: ``MAX_PROMPT_CHARS``, large enough to keep a paragraph's argument together.
MAX_BLOCK_CHARS = 4_000


class UploadTooLargeError(FlashcardsError):
    """Its own type so the router can answer 413 rather than 422."""


def split_text(text: str, limit: int = MAX_BLOCK_CHARS) -> list[str]:
    """Cut one block into pieces no larger than ``limit``.

    Breaks at a paragraph, then a line, then a word, and only mid-word when a
    single "word" is longer than the limit. Whitespace-only pieces are dropped
    rather than shipped as empty excerpts.
    """
    body = text.strip()
    pieces: list[str] = []
    while body:
        if len(body) <= limit:
            pieces.append(body)
            break
        window = body[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut <= 0:
            cut = limit  # one unbroken run longer than the limit
        pieces.append(body[:cut].strip())
        body = body[cut:].strip()
    return [piece for piece in pieces if piece]


def corpus_from_upload(filename: str, source: IO[bytes]) -> list[dict[str, Any]]:
    """Text from one uploaded file, shaped the way ``deck_prompt`` reads it.

    Entries carry only the three meta keys the prompt looks at: ``path`` (the
    file's own name, since it has no vault path), and ``page`` or ``slide``
    where the extractor knew one. Raises :class:`FlashcardsError` for anything
    the user can fix, and :class:`UploadTooLarge` for a body over the cap.
    """
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise FlashcardsError(
            f"{name or 'that file'} is not a kind Argus can read here — "
            f"upload {', '.join(UPLOAD_SUFFIXES)}, or paste the text instead"
        )

    errors: list[str] = []
    # A directory rather than NamedTemporaryFile: the suffix has to survive
    # (extract_blocks dispatches on it) and the whole tree is removed on the way
    # out, including when extraction raises. Nothing here is inside the vault.
    with tempfile.TemporaryDirectory(prefix="argus-deck-") as tmpdir:
        temp_path = Path(tmpdir) / f"upload{suffix}"
        written = 0
        with temp_path.open("wb") as handle:
            while chunk := source.read(COPY_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError(
                        f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                    )
                handle.write(chunk)
        if not written:
            raise FlashcardsError(f"{name} is empty")
        blocks = extract_blocks(temp_path, errors=errors)

    if errors:
        # Surfaced rather than swallowed: extract_blocks turns *any* failure into
        # [] plus a log line, and the readers live in the optional [rag] extra --
        # so a missing dependency would otherwise be reported as "no text".
        raise FlashcardsError(f"could not read {name}: {errors[0]}")
    if not blocks:
        raise FlashcardsError(
            f"no text came out of {name} — a scanned PDF has no text layer to read"
        )

    corpus: list[dict[str, Any]] = []
    for block in blocks:
        meta: dict[str, Any] = {"path": name}
        if block.meta.get("page"):
            meta["page"] = block.meta["page"]
        elif block.meta.get("slide"):
            meta["slide"] = block.meta["slide"]
        # The limit is passed rather than defaulted: a default argument is
        # bound once at import, so it would ignore any later change to the
        # module constant -- including a test's.
        corpus.extend(
            {"text": piece, "meta": dict(meta)}
            for piece in split_text(block.text, MAX_BLOCK_CHARS)
        )

    if not corpus:
        raise FlashcardsError(f"no text came out of {name}")
    return corpus
